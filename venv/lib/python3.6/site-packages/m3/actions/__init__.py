# coding:utf-8
"""
Основные объекты библиотеки: механизмы проверки прав, экшены, паки,
контроллеры, кэш контроллеров
"""
from __future__ import absolute_import
from __future__ import print_function

from functools import wraps
from logging import getLogger
import abc
import importlib
import inspect
import re
import threading
import warnings

from django import http
from django.conf import settings
from m3_django_compat import get_installed_apps
from six import iteritems
from six import string_types
from six import text_type
from six import with_metaclass
from six.moves import map
import django

from .context import ActionContext
from .context import ActionContextDeclaration
from .context import ContextBuildingError
from .context import CriticalContextBuildingError
from .context import DeclarativeActionContext
from .context import RequiredFailed
from .exceptions import ActionException
from .exceptions import ActionNotFoundException
from .exceptions import ActionPackNotFoundException
from .exceptions import ActionUrlIsNotDefined
from .exceptions import ApplicationLogicException
from .exceptions import ReinitException
from .metrics import create_statsd_client
from .results import ActionRedirectResult
from .results import ActionResult
from .results import BaseContextedResult
from .results import HttpReadyResult
from .results import JsonResult
from .results import OperationResult
from .results import PreJsonResult
from .results import TextResult
from .results import XMLResult


logger = getLogger('django')


_STATSD_CLIENT = create_statsd_client(settings)


ACD = ActionContextDeclaration


def _clean_url(s):
    return re.sub(r'^[/^]*(.*?)[$/]*$', r'\1', s)


def _import_by_path(path):
    """
    Импортирует объект по пути @path вида "full.package.path.to.some_object"
    """
    match = re.match(r'^(?:(.*)\.)?([^.]+)$', path)
    if match is None:
        raise ValueError(u'Wrong path to import: %r' % path)
    module_name, obj_name = match.groups()
    module = importlib.import_module(module_name)
    return getattr(module, obj_name)


def _name_of(obj):
    """
    Возвращает для указанного объекта имя вида
    "package.ClassOfObject"
    или
    "package.ClassOfParent.ClassOfObject", если obj имеет атрибут "parent"
    """

    def cls_name(obj):
        return (obj if inspect.isclass(obj) else obj.__class__).__name__

    parent = getattr(obj, 'parent', None)
    if parent:
        parent = '%s.' % cls_name(parent)
    else:
        parent = ''

    package = inspect.getmodule(obj).__name__

    obj = cls_name(obj)

    return '%s.%s%s' % (package, parent, obj)


def _cached_to(attr_name):
    """
    Оборачивает простые методы (без аргументов) и property getters,
    с целью закэшировать первый полученный результат
    """
    def wrapper(fn):
        @wraps(fn)
        def inner(self):
            if hasattr(self, attr_name):
                result = getattr(self, attr_name)
            else:
                result = fn(self)
                setattr(self, attr_name, result)
            return result
        return inner
    return wrapper


def _must_be_replaced_by(alternative):
    """
    Оборачивает устаревший метод таким образом,
    что при вызове метода выводится предупреждение
    с рекомендацией замены метода на текущую альтернативу
    """
    def wrapper(fn):
        @wraps(fn)
        def inner(self, *args, **kwargs):
            class_name = self.__class__.__name__
            # if '%' in alternative:
            #     alternative = alternative % class_name
            warnings.warn(
                "'{clazz}.{method}' deprecated! Use '{alternative}'!".format(
                    clazz=class_name,
                    method=fn.__name__,
                    alternative=alternative
                ), FutureWarning, 2)
            return fn(self, *args, **kwargs)
        return inner
    return wrapper


# =============================================================================
# Абстрактный механизм проверки прав и его реализация,
# проверяющая права через механизм django.contrib.auth.models.User.has_perm
# =============================================================================


class AbstractPermissionChecker(with_metaclass(abc.ABCMeta, object)):
    """
    Абстрактный механизм проверки прав
    """

    @abc.abstractmethod
    def has_action_permission(self, request, action, subpermission=None):
        """
        Метод должен возвращать True, если выполнение экшена
        допустимо в контексте запроса

        :param request: запрос
        :type request: django.http.Request

        :param action: Экшен, наличие прав на выполнение которого проверяется
        :type action: m3_core.actions.Action

        :return: Допустимость выполнения экшена в контесте запроса
        :rtype: True или False
        """
        pass

    @abc.abstractmethod
    def has_pack_permission(self, request, pack, permission):
        """
        Метод должен возвращать True, если действие,
        характеризуемое парой pack/permission,
        допустимо в контексте запроса

        :param request: запрос
        :type request: django.http.Request

        :param pack: пак, для которого возвращается код доступа
        :type pack: m3_core.actions.ActionPack

        :return: Допустимость пака в контесте запроса
        :rtype: True или False
        """
        pass

    @staticmethod
    def get_perm_code(action_or_pack, subpermission=None):
        '''
        Возвращает код действия, для контроля прав доступа

        :param action_or_pack: экшен или пак, для которого
            возвращается код доступа
        :type action_or_pack: m3_core.actions.Action либо
            m3_core.actions.ActionPack

        :param basestring subpermission: код подправа

        :return: код действия для контроля прав доступа
        :rtype: basestring
        '''
        code = _name_of(action_or_pack)
        # уберем из кода префикс системы,
        # т.к. код права должен быть относительным
        if hasattr(settings, 'ROOT_URL') and (
            code.startswith(settings.ROOT_URL)
        ):
            code = code[len(settings.ROOT_URL):]
        if subpermission:
            code = '%s#%s' % (code, subpermission)
        return code


class AuthUserPermissionChecker(AbstractPermissionChecker):
    """
    Backend, проверяющий права через
    django.contrib.auth.models.User.has_perm

    Требует подключения в проекте session-middleware, т.к. опирается
    на наличие в request атрибута 'user',
    указывающего на текущего пользователя
    """
    def has_action_permission(self, request, action, subpermission=None):

        # контракт
        assert isinstance(action, Action)
        assert isinstance(action.parent, ActionPack)
        if subpermission:
            assert isinstance(subpermission, str)

        # если ползователя нет (аноним) - прав тоже нет
        if not request.user:
            return False

        result = True
        if action.need_check_permission:
            if subpermission:
                assert (
                    subpermission in action.sub_permissions or
                    subpermission in action.parent.sub_permissions
                ), (
                    u'Подправо ("%s") должно быть описано в экшне или паке'
                    % subpermission
                )
                result = request.user.has_perm(
                    action.get_perm_code(subpermission))
            else:
                result = request.user.has_perm(action.get_perm_code())
        return result

    def has_pack_permission(self, request, pack, permission):
        assert isinstance(pack, ActionPack)
        if permission:
            assert isinstance(permission, str)

        result = True
        if pack.need_check_permission:
            assert permission in pack.sub_permissions, (
                u'Подправо ("%s") должно быть описано в паке' % permission)
            result = request.user.has_perm(pack.get_perm_code(permission))
        return result


class BypassPermissionChecker(AbstractPermissionChecker):
    """
    Механизм проверки прав, "разрешающий всем и всё"
    """
    def has_action_permission(self, request, action, subpermission=None):
        return True

    def has_pack_permission(self, request, pack, permission):
        return True


class LegacyPermissionChecker(AbstractPermissionChecker):
    """
    Мех-м проверки прав, созданный для совместимости со старыми проектами
    """
    @staticmethod
    def get_perm_code(pack_or_action, subpermission=None):
        code = pack_or_action.get_absolute_url()
        # уберем из кода префикс системы,
        # т.к. код права должен быть относительным
        if hasattr(settings, 'ROOT_URL') and (
                code.startswith(settings.ROOT_URL)):
            code = code[len(settings.ROOT_URL):]
        if subpermission:
            code = '%s#%s' % (code, subpermission)
        return code

    def has_action_permission(self, request, action, subpermission=None):
        '''
        Проверка пака на выполнение действия для указанного пользователя
        '''
        assert isinstance(action.parent, ActionPack)
        # Если в наборе действий need_check_permission=True,
        # а в самом действии False, то права не проверяются
        # Если в наборе действий need_check_permission=True,
        # и в самом действии True, то права проверяются
        # Если в наборе действий need_check_permission=False,
        # а в самом действии True, то права не проверяются
        # Если в наборе действий need_check_permission=False,
        # а в самом действии False, то права не проверяются
        # Т.е. права проверяются только,
        # если в наборе действий и в действии включена проверка прав
        # Признак need_check_permission в вышестоящих наборах действий
        # не влияет на решение, т.к. в тех наборах свои действия

        # Проверим, что в действии и наборе разрешена проверка прав
        user_obj = request.user
        code = self.get_perm_code(action, subpermission)
        if action.need_check_permission and (
                action.parent.need_check_permission):
            # если пользователя нет, значит аноним - ему дадим отпор
            if user_obj:
                # проверим что права на выполнение есть
                return user_obj.has_perm(code)
            else:
                return False
        else:
            return True

    def has_pack_permission(self, request, pack, permission):
        '''
        Проверка на внутреннее право пака для указанного пользователя
        '''
        assert isinstance(permission, str)
        # Подчиненные права набора действий
        # проверяются только в случае разрешения проверки в наборе действий
        # Если переданный код не прописан в правах этого действия,
        # то это не наш код - значит всё разрешено
        user_obj = request.user
        if pack.need_check_permission and permission in pack.sub_permissions:
            # если пользователя нет, значит аноним - ему дадим отпор
            if user_obj:
                # проверим что права на выполнение есть
                return user_obj.has_perm(self.get_perm_code(pack, permission))
            else:
                return False
        return True


class LazyContainer(object):
    """
    Ленивая обёртка для объектов, указываемых в settings.
    Предназначена для позднеё загрузки объектов - по первому обращению
    """

    def __init__(self, fabric):
        self._fabric = fabric

    def _get_obj(self):
        if not hasattr(self, '_cache'):
            self._cache = self._fabric()
        return self._cache

    def __getattr__(self, attr):
        if attr == '_cache':
            return super(LazyContainer, self).__getattribute__(attr)
        else:
            return getattr(self._get_obj(), attr)


def _permission_checker_fabric():
    """
    Получение экземпляра permission checker
    """
    _OLD_PERMISSION_CHECKING = getattr(
        settings, 'CONTROLLER_SHOULD_USE_OLD_PERMISSION_CHECKING', False)

    if _OLD_PERMISSION_CHECKING:
        # backend проверки прав, совместимый со старыми проектами
        result = LegacyPermissionChecker
    else:
        path = getattr(settings, 'M3_PERMISSION_CHECKER', None)
        if path is None:
            # если backend не задан в настройках, то исходим из того,
            # подключены ли пользователи Django
            if 'django.contrib.auth' in get_installed_apps():
                result = AuthUserPermissionChecker
            else:
                result = BypassPermissionChecker
        else:
            # в настройках можно указать свой backend для проверки прав
            if isinstance(path, str):
                # указан строкой - импортируем
                result = _import_by_path(path)
            if not (inspect.isclass(result) and issubclass(
                    result, AbstractPermissionChecker)):
                raise TypeError(
                    u'M3_PERMISSION_CHECKER option value must be '
                    u'a string or class!'
                )
    # бэкенд инстанцируется для дальнейшего использования
    return result()


# ленивый экземпляр бэкенда проверки прав
_permission_checker = LazyContainer(_permission_checker_fabric)


# ========================== ИСКЛЮЧЕНИЯ =======================================

# =============================================================================
class Action(object):
    u"""
    Базовый класс, от которого должны наследоваться все Action'ы в системе.
    Заменяет собой классические вьюшки Django.
    """

    #: Часть адреса запроса которая однозначно определяет его принадлежность к
    #: конкретному Action'у
    url = ''

    #: Ссылка на ActionPack к которому принадлежит данный Action
    parent = None

    #: Ссылка на контроллер к которому принадлежит данный Action
    controller = None

    #: Наименование действия для отображения
    verbose_name = None

    #: Признак обработки прав доступа,
    #: при выполнении действия (по-умолчанию отключен)
    #: Как обрабатывается этот признак - смотри в has_permission
    need_check_permission = False

    #: Словарь внутренних прав доступа, используемых в действии
    #: ключ - код права, который совмещается с кодом действия
    #: значение - наименование права
    #: Пример: {'tab2':u'Редактирование вкладки Доп. сведения',
    #: 'work_visible':u'Просмотр сведений о работе'}
    #: Общий код права доступа будет иметь вид:
    #: /edit#tab2 и /edit#work_visible соответственно
    #: Как обрабатывается этот список - смотри в has_sub_permission
    sub_permissions = {}

    #: Логический путь действия в прикладной системе.
    #: Используется только для отображения и группировки
    #: действий с одинаковым путем.
    #: Также может использоваться для создания меню.
    #: Например, путь может быть: "Справочники\Общие" или "Реестры"
    path = None

    @_must_be_replaced_by('%s.get_perm_code')
    def get_sub_permission_code(self, sub_code):
        # метод оставлен для совместимости
        return self.get_perm_code(sub_code)

    @_must_be_replaced_by('%s.has_perm')
    def has_sub_permission(self, user_obj, sub_code, request):
        # метод оставлен для совместимости
        return self.has_perm(request, sub_code)

    @_must_be_replaced_by('%s.get_perm_code')
    def get_permission_code(self):
        # метод оставлен для совместимости
        return self.get_perm_code()

    @_must_be_replaced_by('%s.has_perm')
    def has_permission(self, user_obj, request):
        # метод оставлен для совместимости
        return self.has_perm(request)

    # ================= НОВЫЙ ИНТЕРФЕЙС ПРОВЕРКИ ПРАВ =========================
    def has_perm(self, request, subpermission=None):
        """
        Интерфейсный метод проверки (под)прав экшна.

        Использовать ВМЕСТО has_permission/has_sub_permission

        :param request: запрос
        :type request: django.http.Request
        """
        return _permission_checker.has_action_permission(
            request, self, subpermission)

    def get_perm_code(self, subpermission=None):
        """
        Получение кода (под)права
        """
        # по-умолчанию код генерится backend`ом
        return _permission_checker.get_perm_code(self, subpermission)
    # =========================================================================

    def pre_run(self, request, context):
        """
        Метод для предварительной обработки
        входящего запроса и контекста,
        перед передачений в run().
        Если возвращает значение отличное от None,
        обработка запроса прекращается и результат
        уходит во вьюшку контроллера.

        :param request: запрос
        :type request: django.http.Request

        :param context: Контекст выполнения операции, восстанавливаемый
            из контекств
        :type context: m3_core.actions.context.ActionContext

        :return: None либо объект для обработке во вьюшке контроллера
        """
        pass

    def post_run(self, request, context, response):
        """
        Метод для постобработка результата работы экшенаа.

        :param request: запрос
        :type request: django.http.Request

        :param context: Контекст выполнения операции,
            востанавливаемый из контекств
        :type context: m3_core.actions.context.ActionContext

        :param response: результат выполнения экшена
        :type response: наследник m3_core.actions.results.ActionResult
        """
        pass

    def context_declaration(self):
        """
        Метод объявления необходимости наличия
        определенных параметров в контексте.

        Должен возвращать список из экземпляров *ActionContextDeclaration*
        либо
        словарь описания контекста для *DeclarativeActionContext*

        :return: описание необходимости наличия определенных параметров
            в запросе
        :rtype: list of m3_core.actions.context.ActionContextDeclaration либо
            m3_core.actions.context.DeclarativeActionContext
        """
        pass

    def run(self, request, context):
        """
        Обеспечивает непосредственное исполнение запроса
        (аналог views в Django).
        Обязательно должен быть перекрыт в наследнике.

        :param request: запрос
        :type request: django.http.Request

        :param context: Контекст выполнения операции,
            востанавливаемый из контекств
        :type context: m3_core.actions.context.ActionContext

        :return: результат выполнения экшена
        :rtype: наследник m3_core.actions.results.ActionResult
        """
        raise NotImplementedError()

    @classmethod
    def absolute_url(cls):
        '''
        Возвращает полный путь до действия.
        НО при условии что этот экшен используется
        ТОЛЬКО В ОДНОМ ПАКЕ И КОНТРОЛЛЕРЕ, иначе валим всех!
        Ищет перебором!

        :return: полный путь до действия
        :rtype: basestring
        '''
        url = ControllerCache.get_action_url(cls)
        if not url:
            raise ActionNotFoundException(clazz=cls)
        return url

    def get_packs_url(self):
        """
        Возвращает строку, полный адрес от контроллера до текущего экшена

        :return: полный адрес от контроллера до текущего экшена
        :rtype: basestring
        """
        assert isinstance(self.parent, ActionPack)
        path = []
        pack = self.parent
        while pack is not None:
            path.append(pack.url)
            pack = pack.parent
        return ''.join(reversed(path))

    def get_absolute_url(self):
        '''
        Возвращает полный путь от хоста до конечного экшена
        @deprecated: Дублирует absolute_url
        '''
        # TODO: Переписать, т.к. этот код дублирует функции контроллера
        assert isinstance(self.controller, ActionController), (
            '%s is not actioncontroller in %s' % (self.controller, self))
        # Очищаем от мусора рег. выр.
        url = self.url.replace('$', '')
        return self.controller.url + self.get_packs_url() + url

    @classmethod
    def get_verbose_name(cls):
        return cls.verbose_name if cls.verbose_name else cls.get_short_name()

    @classmethod
    def get_short_name(cls):
        return _name_of(cls)


class ActionPack(object):
    '''
    Базовый класс для всех ActionPack'ов.
    Предназначен для хранения в себе других
    экшенов и паков, схожих по целям.
    '''
    url = ''

    # Адрес экшенпака
    @classmethod
    def get_short_name(cls):
        """
        Имя пака для поиска в ControllerCache

        :return: Имя пака
        :rtype: basestring
        """
        name = cls.__dict__.get('_auto_short_name')
        if not name:
            name = cls._auto_short_name = _name_of(cls)
        return name

    @property
    @_cached_to('__cached_short_name')
    def short_name(self):
        # имя пака для поиска в ControllerCache в виде атрибута
        # для совместимости с m3
        return self.get_short_name()

    @classmethod
    def absolute_url(cls):
        """
        Возвращает полный адрес (url) от контроллера до текущего экшенпака
        """
        path = [cls.url]
        pack = cls.parent
        while pack is not None:
            path.append(pack.url)
            pack = pack.parent
        url = ''.join(reversed(path))

        contr_url = ''
        for cont in ControllerCache.get_controllers():
            p = cont.find_pack(cls)
            if p:
                contr_url = cont.url
                break
        return contr_url + url

    #: Ссылка на вышестоящий пакет, тот в котором зарегистрирован данный пакет
    parent = None

    #: Ссылка на родительский ActionController
    controller = None

    #: Наименование Набора действий для отображения
    verbose_name = None

    #: Признак обработки прав доступа,
    #: при выполнении дочерних действий (по-умолчанию отключен)
    #: Как обрабатывается этот признак - смотри в Action.has_permission
    need_check_permission = False

    #: Словарь внутренних прав доступа, используемых в наборе действий
    #: ключ - код права, который совмещается с адресом (кодом) набора действий
    #: значение - наименование права
    #: Пример: {'edit':u'Редактирование записи'}
    #: Общий код права доступа будет иметь вид: /users#edit
    #: Как обрабатывается этот список - смотри в has_sub_permission
    sub_permissions = {}

    #: Логический путь набора действий в прикладной системе.
    #: Используется только для отображения
    #: и группировке наборов с одинаковым путем.
    #: Также может использоваться для создания меню.
    #: Например, путь может быть: "Справочники\Общие" или "Реестры"
    path = None

    def __init__(self):
        #: Список действий зарегистрированных на исполнение в данном пакете
        self.actions = []
        #: Список дочерних пакетов (подпакетов),
        #: зарегистрированных на исполнение в данном пакете
        self.subpacks = []

    def get_absolute_url(self):
        """
        Возвращает абсолютный путь (НОРМАЛЬНО, в отличие от absolute_url)
        """
        assert self.controller
        pack = self
        if pack.parent:
            path = []
            while pack.parent:
                path.insert(0, pack.url)
                pack = pack.parent
        else:
            path = [pack.url]
        return '/'.join([pack.controller.url] + list(map(_clean_url, path)))

    def get_permission_code(self):
        # метод оставлен для совместимости
        return self.get_perm_code()

    def get_sub_permission_code(self, sub_code):
        # метод оставлен для совместимости
        return self.get_perm_code(sub_code)

    def has_sub_permission(self, user_obj, sub_code, request):
        # метод оставлен для совместимости

        return self.has_perm(request, sub_code)

    # ================= НОВЫЙ ИНТЕРФЕЙС ПРОВЕРКИ ПРАВ =========================
    def has_perm(self, request, permission):
        """
        Интерфейсный метод проверки подправ пака.

        Использовать ВМЕСТО has_sub_permission

        :param request: запрос
        :type request: django.http.Request
        """
        return _permission_checker.has_pack_permission(
            request, self, permission)

    def get_perm_code(self, permission=None):
        """
        Получение кода (под)права
        """
        # по-умолчанию код генерится backend`ом
        return _permission_checker.get_perm_code(self, permission)
    # =========================================================================

    def pre_run(self, request, context):
        """
        Метод для предварительной обработки
        входящего запроса и контекста
        перед передачений в нижестоящий экшен или пак.
        Если возвращает значение отличное от None, обработка запроса
        прекращается и результат уходит во вьюшку контроллера.

        :param request: запрос
        :type request: django.http.Request

        :param context: Контекст выполнения операции, восстанавливаемый
            из контекств
        :type context: m3_core.actions.context.ActionContext
        """
        pass

    def post_run(self, request, context, response):
        '''
        Метод для постобработки результата работы вышестоящего экшена или пака.
        Принимает исходный запрос *request*, результат работы *response* и
        извлеченный контекст *context*.

        :param request: запрос
        :type request: django.http.Request

        :param context: Контекст выполнения операции, восстанавливаемый
            из контекств
        :type context: m3_core.actions.context.ActionContext

        :param response: ответ, полученный в результате выполнения
            действия
        :type response: m3_core.actions.results.ActionResult
        '''
        pass

    @classmethod
    def get_verbose_name(cls):
        """
        Получение понятного имени пака

        :rtype: unicode
        """
        return (
            cls.title if hasattr(cls, 'title') and cls.title
            else cls.verbose_name if cls.verbose_name else cls.__name__
        )


class ActionController(object):
    '''
    Класс коонтроллер - обеспечивает обработку
    пользовательских запросов путем передачи
    их на исполнение соответствущим Action'ам
    '''
    #: Наименование Контроллера для отображения
    verbose_name = None

    class FakePacks:
        """
        Класс содержит заглушки методов, чтобы инспектор кода не ругался,
        т.к. настоящие методы присваиваются классу в рантайме.
        """
        def append(self, item):
            pass

        def extend(self, items_list):
            pass

    def __init__(self, url="", name=None):
        '''
        :param basetring url: используется для отсечения лишней части пути в
            запросе, поскольку управление в пак передается из вьюшки
        :param basestring name: человеческое название контроллера.
            Используется для отладки.
        '''
        #: ДЛЯ СОВМЕСТИМОСТИ.
        #: Имитирует список паков торчащий наружу
        self.packs = self.FakePacks()
        self.packs.append = self.append_pack
        self.packs.extend = self.extend_packs

        # Используется для отсечения лишней части пути в запросе
        self.url = url
        self.name = name

        # Словарь для быстрого разрешения запросов.
        # Состоит из полного пути запроса, списка
        # вызываемых паков и экшена.
        # Пример: {'/dict/lpu/get_rows': ([DictPack, LPUPack], RowsAction)}
        self._url_patterns = {}

        # Словари для быстрого поиска паков по имени и классу, например:
        # {'DictPack', <DictPack instance at 0x01FBACB0>}
        self._packs_by_name = {}
        self._packs_by_type = {}
        self._actions_by_type = {}
        self._actions_by_name = {}
        self._nodes_by_perm = {}
        # TODO: Тоже самое можно добавить для short_name
        # в экшенах вместо m3.helpers.urls

        self.top_level_packs = []

        # Блокировка для перестроения паттернов урлов
        self._rebuild_lock = threading.RLock()

        # Признак того, что контроллер зарегистрирован во внутреннем кеше
        self._registered = False

    def __str__(self):
        return (
            self.name if self.name
            else super(ActionController, self).__str__()
        )

    # =========================================================================
    # Методы для быстрого поиска экшенов и паков по разным атрибутам
    # =========================================================================
    def _add_action_to_search_dicts(self, action, full_path):
        """ Добавляет экшен в словари для быстрого доступа """
        assert isinstance(action, Action)
        self._actions_by_name[action.get_short_name()] = (action, full_path)
        self._actions_by_type[action.__class__] = (action, full_path)
        self._nodes_by_perm[action.get_perm_code()] = action

    def _add_pack_to_search_dicts(self, pack):
        """ Добавляет экшенпак в словари для быстрого доступа """
        assert isinstance(pack, ActionPack)
        self._packs_by_name[pack.get_short_name()] = pack
        self._packs_by_type[pack.__class__] = pack
        self._nodes_by_perm[pack.get_perm_code()] = pack

    def _rebuild_search_dicts(self):
        """
        Полностью перестраивает поисковые словари.
        Бывает нужно, если иерархия экшенов непредсказуемо изменяется
        """
        self._actions_by_name.clear()
        self._actions_by_type.clear()
        for full_path, v in iteritems(self._url_patterns):
            _, action = v
            self._add_action_to_search_dicts(action, full_path)
        # Обновлять _packs_by_name и _packs_by_type не нужно!

    # =========================================================================
    # Методы формирующие иерархию экшенов и паков
    # =========================================================================
    def _load_class(self, full_path):
        '''
        По полному пути загружает и созвращает класс
        '''
        # Получаем имя модуля и класса в нём
        dot = full_path.rindex('.')
        mod_name = full_path[:dot]
        pack_name = full_path[dot + 1:]
        # Пробуем загрузить
        mod = importlib.import_module(mod_name)
        clazz = getattr(mod, pack_name)
        return clazz

    def _build_pack_node(self, clazz, stack):
        # Что-бы нам не передали, нужно создать экземпляр
        if isinstance(clazz, str):
            clazz = self._load_class(clazz)()
        elif inspect.isclass(clazz):
            clazz = clazz()

        # Отладочный атрибут built нужен,
        # чтобы обнаружить повторное перестроение экшенов
        if hasattr(clazz, '_built'):
            raise ReinitException(clazz=clazz)
        clazz._built = True

        # Присваиваем родителя
        if len(stack) > 0:
            clazz.parent = stack[-1]

        if isinstance(clazz, ActionPack):
            # Для быстрого поиска
            self._add_pack_to_search_dicts(clazz)

            stack.append(clazz)
            # Бежим по экшенам
            for action in clazz.actions:
                self._build_pack_node(action, stack)
            # Бежим по пакам
            for pack in clazz.subpacks:
                self._build_pack_node(pack, stack)
                pack.controller = self
            stack.pop()
        else:
            clazz.controller = self
            full_path = self._build_full_path(stack, clazz)

            # Для быстрого поиска
            self._add_action_to_search_dicts(clazz, full_path)

            self._url_patterns[full_path] = (stack[:], clazz)

    def _invoke(self, request, action, stack):
        '''
        Непосредственный вызов экшена с отработкой всех событий

        :param request: запрос
        :type request: django.http.Request
        '''

        # проверим что права на выполнение есть
        allowed = _permission_checker.has_action_permission(request, action)

        if not allowed:
            # Стандартное сообщение об отсутствии прав
            msg = u'У вас нет прав на выполнение этого действия!'

            # Если разработчик указал verbose_name у action-а,
            # то добавляем название действия
            if action.verbose_name:
                msg = ' '.join([msg, u'Действие:', action.verbose_name])

            return OperationResult.by_message(msg)

        # Заполняем контект и проверяем его
        rules = action.context_declaration()
        context = self.build_context(request, rules)
        try:
            context.build(request, rules)
        except CriticalContextBuildingError:
            # критическая ошибка сбора контекста - должна валиться
            raise
        except ContextBuildingError as e:
            # некритичную ошибку - показываем пользователю
            return OperationResult.by_message(text_type(e))
        except RequiredFailed as e:
            # если контекст неправильный, то возвращаем
            # фейльный результат операции
            return OperationResult.by_message(
                u'Не удалось выполнить операцию. '
                u'Не задан обязательный<br>параметр: ' + e.reason
            )

        # В request заносим информацию о паках и экшене, которые будут
        # выполнены в процессе обработки запроса
        request.target_packs = stack
        request.target_action = action

        try:
            # Все ПРЕ обработчики
            for pack in stack:
                result = pack.pre_run(request, context)
                if result is not None:
                    return result
            # Сам экшен
            result = action.pre_run(request, context)
            if result is not None:
                return result
            response = action.run(request, context)
            result = action.post_run(request, context, response)
            if result is not None:
                return result
            # Все ПОСТ обработчики с конца
            for pack in reversed(stack):
                result = pack.post_run(request, context, response)
                if result is not None:
                    return result
        except ApplicationLogicException as exc:
            return OperationResult(
                success=False, message=exc.exception_message)

        # по возможности запихиваем текущий контекст в response
        if isinstance(response, BaseContextedResult):
            response.set_context(context)
        return response

    def process_request(self, request):
        """
        Обработка входящего запроса *request* от клиента.
        Обрабатывается по аналогии с UrlResolver'ом Django

        :param request: запрос
        :type request: django.http.Request

        :return: результат выполнения экшена
        :rtype: наследник m3_core.actions.results.ActionResult

        :raise: http.Http404
        """
        ControllerCache.populate()

        path = request.path
        matched = self._url_patterns.get(path)
        if matched:
            stack, action = matched

            with _STATSD_CLIENT(self, request):
                try:
                    result = self._invoke(request, action, stack)
                except:  # noqa
                    if settings.DEBUG:
                        # Записывает сообщение в логгер если включен тестовый
                        # режим
                        logger.exception(
                            u'ActionController.process_request: '
                            u'перехвачена необработанная ошибка'
                        )
                    raise

            if isinstance(result, ActionResult):
                return result.get_http_response()

            # Если вернули редирект,
            # то нужно повторно обработать запрос, уже с новым экшеном
            elif isinstance(result, ActionRedirectResult):
                new_path = self.get_action_url(result.action)
                if not new_path:
                    raise ActionNotFoundException(result.action)

                request.path = new_path
                result.prepare_request(request)
                return self.process_request(request)

            return result

        raise http.Http404()

    def build_context(self, request, rules):
        '''
        Выполняет построение контекста вызова операции ActionContext
        на основе переданного request

        :param request: запрос
        :type request: django.http.Request

        :param dict rules: построение контекста

        :return: пустой экземпляр контекста
        :rtype: m3_core.actions.context.DeclarativeActionContext()
            или m3_core.actions.context.ActionContext
        '''
        if DeclarativeActionContext.matches(rules):
            return DeclarativeActionContext()
        else:
            return ActionContext()

    # =========================================================================
    # Методы, предназначенные для поиска экшенов и паков в контроллере
    # =========================================================================
    @_must_be_replaced_by('ControllerCache.find_pack')
    def find_pack(self, type):
        return self._find_pack(type)

    @_must_be_replaced_by('ControllerCache.find_action')
    def find_action(self, type):
        return self._find_action(type)

    def _find_pack(self, pack):
        if isinstance(pack, string_types):
            inst = self._packs_by_name.get(pack)
        elif inspect.isclass(pack):
            inst = self._packs_by_type.get(pack)
        else:
            raise ValueError('Wrong type of argument %s' % pack)
        return inst

    def _find_action(self, action):
        if isinstance(action, string_types):
            inst, _ = self._actions_by_name.get(action, (None, None))
        elif inspect.isclass(action):
            inst, _ = self._actions_by_type.get(action, (None, None))
        else:
            raise ValueError('Wrong type of argument %s' % action)
        return inst

    def get_action_url(self, type):
        """
        Возвращает полный URL адрес для класс или имени класса экшена *action*
        """
        ControllerCache.populate()

        if isinstance(type, str):
            _, full_path = self._actions_by_name.get(type, (None, None))
        elif issubclass(type, Action):
            _, full_path = self._actions_by_type.get(type, (None, None))
        else:
            raise ValueError('Wrong type of argument %s' % type)

        return full_path

    def _find_node_by_perm(self, perm):
        """
        Возвращает экшн/пак по коду права
        """
        return self._nodes_by_perm.get(perm)

    # =========================================================================
    # Методы, предназначенные для добавления/изменения/удаления
    # пакетов действий в контроллер
    # =========================================================================
    def append_pack(self, pack):
        """
        Добавляет пак в контроллер.

        :param pack: пак, который добавляется в контроллер
        :type pack: m3_core.actions.ActionPack
        """
        if not isinstance(pack, ActionPack):
            raise TypeError(
                u'Pack must be an instance of ActionPack subclass!')

        # пак может быть перегруже
        if pack.get_short_name() in ControllerCache.overrides:
            pack = ControllerCache.overrides[pack.get_short_name()]

        self._build_pack_node(pack, [])
        if pack not in self.top_level_packs:
            self.top_level_packs.append(pack)
            pack.controller = self
        ControllerCache.register_controller(self)
        return pack

    def extend_packs(self, packs):
        '''
        Производит массовое добавление экшенпаков в контроллер.

        :param packs: список паков,
            которые необходимо зарегистрировать в контроллере
        :type packs: список объектов m3_core.actions.ActionPack
        '''
        for pack in packs:
            self.append_pack(pack)

    def _norm_url(self, url):
        '''
        Очищает части адреса от мусора.
        Раньше были планы использовать регулярные выражения в адресах,
        сейчас остался мусор от них.
        '''
        for char in ['^', '&', '$', '/']:
            if url.startswith(char):
                url = url[1:]
            if url.endswith(char):
                url = url[:-1]
        return '/' + url if url else ''

    def _build_full_path(self, packs_list, final_action):
        '''
        Возвращает полный адрес от контроллера через паки до конечного экшена.
        '''
        if not final_action.url:
            raise ActionUrlIsNotDefined(final_action)
        return self._norm_url(self.url) + ''.join([
            self._norm_url(x.url) for x in packs_list]
        ) + self._norm_url(final_action.url)

    def wrap_pack(self, dest_pack, wrap_pack):
        '''
        Вставляет экшенпак wrap_pack внутрь иерархии перед dest_pack.
        Таким образом можно перехватывать запросы и ответы пака dest_pack.


        :param dest_pack: Пак который будем оборачивать
        :type dest_pack: m3_core.actions.ActionPack

        :param wrap_pack: Оборачивающий пак
        :type wrap_pack: m3_core.actions.ActionPack
        '''
        assert (
            issubclass(dest_pack, ActionPack) and
            issubclass(wrap_pack, ActionPack)
        )

        """
        Допустим есть цепочка паков:
        A1 - X - A2 - A3    |   A1 - Y - X - A2 - A3
        B1 - B2 - X         |   B1 - B2 - Y - X
        X - C1 - C2         |   Y - X - C1 - C2
        Для решения нужно:
        1. Найти экземпляры пака X
        2. В цепочку вместо X вставить Y->X c учетом левых и правых участников
        3. Перестроить адреса пробежавшись по цепочке
        """
        wrapper = wrap_pack()
        self._add_pack_to_search_dicts(wrapper)
        new_patterns = {}
        current_packs_slice = None
        # списки соседей
        left_packs = []
        right_packs = []

        for url, value in iteritems(self._url_patterns):
            packs_list, final_action = value

            # Поиск пака и соседей в списке
            left_pack = None
            for pos, pack in enumerate(packs_list):
                if pack.__class__ == dest_pack:
                    if pos > 0:
                        left_pack = packs_list[pos - 1]
                    break
            else:
                # Просто копируем
                new_patterns[url] = value
                continue

            # Мутация соседей
            pack.parent = wrapper
            packs_list.insert(pos, wrapper)
            if left_pack:
                wrapper.parent = left_pack
                left_packs.append(left_pack)
            right_packs.append(pack)

            # Создание нового урла
            full_path = self._build_full_path(packs_list, final_action)
            new_patterns[full_path] = (packs_list[:], final_action)
            current_packs_slice = packs_list[:pos + 1]

        self._url_patterns = new_patterns

        # У враппера могут быть собственные экшены и паки.
        # Их тоже нужно построить. Но врапперы также могут быть наследованы
        # от оригинальных паков, поэтому не нужно перестраивать
        # уже существующие экшены и паки, только заменить у них родителя
        if current_packs_slice:
            for subpack in wrapper.subpacks:
                if subpack not in pack.subpacks:
                    self._build_pack_node(subpack, current_packs_slice)

            for action in wrapper.actions:
                if action not in pack.actions:
                    self._build_pack_node(action, current_packs_slice)

            # добавим в левые паки наш врапер,
            # как подчиненный пак без перестройки узлов
            # это нужно для корректной навигации сверху-вниз,
            # а иначе мы никак не узнаем какие паки ниже
            for pack in left_packs:
                if wrapper not in pack.subpacks:
                    pack.subpacks.append(wrapper)
            for pack in right_packs:
                if pack not in wrapper.subpacks:
                    wrapper.subpacks.append(pack)
        else:
            raise ActionPackNotFoundException(dest_pack)

        self._rebuild_search_dicts()

    def wrap_action(self, dest_pack, dest_action, wrap_pack):
        '''
        Вставляет перед экшеном dest_action,
        входящим в пак dest_pack, промежуточный пак wrap_pack.

        ВНИМАНИЕ! Экшены как правило обращаются к своим пакам
        через атрибут "parent", поэтому, вероятно, будут возникать ошибки,
        из-за того, что оборачивающий пак
        не предоставляет методы изначального пака.
        Оборачивающий пак можно наследовать от оригинального,
        но тогда вместо оборачивая целесообразно использовать подмену паков.

        :param dest_pack: Пак в который входит оборачиваемый экшен
        :type dest_pack: m3_core.actions.ActionPack

        :param dest_action: Оборачиваемый экшен
        :type dest_action: m3_core.actions.Action

        :param wrap_pack: Оборачивающий пак
        :type wrap_pack: m3_core.actions.ActionPack
        '''
        assert (
            issubclass(dest_pack, ActionPack) and
            issubclass(wrap_pack, ActionPack) and
            issubclass(dest_action, Action)
        )
        wrapper = wrap_pack()
        self._add_pack_to_search_dicts(wrapper)
        new_patterns = {}
        current_packs_slice = None

        for url, value in iteritems(self._url_patterns):
            packs_list, final_action = value

            # Поиск исходного пака и экшена в нём
            last_pack = packs_list[-1]
            if last_pack.__class__ == dest_pack and (
                    final_action.__class__ == dest_action):
                # Вставка
                packs_list.append(wrapper)
                wrapper.parent = last_pack
                final_action.parent = wrapper

                # Создание нового урла
                full_path = self._build_full_path(packs_list, final_action)
                new_patterns[full_path] = (packs_list[:], final_action)
                current_packs_slice = packs_list

            else:
                # Просто копируем
                new_patterns[url] = value

        self._url_patterns = new_patterns

        if current_packs_slice:
            # У враппера могут быть собственные экшены и паки.
            # Их тоже нужно построить.
            for subpack in wrapper.subpacks:
                self._build_pack_node(subpack, current_packs_slice)
            for action in wrapper.actions:
                self._build_pack_node(action, current_packs_slice)
        else:
            raise ActionPackNotFoundException(dest_pack)

        self._rebuild_search_dicts()

    def dump_urls(self):
        '''
        Отладочный метод.
        Выводит в консоль список всех адрес зарегистрированных в контроллере.
        '''
        print('==== CONTROLLER WITH URL: %s ======' % self.url)
        for key in sorted(self._url_patterns.keys()):
            print(key)
        print()
        print('Total patterns %s' % len(list(self._url_patterns.keys())))

    def get_action_by_url(self, url):
        """
        Получить Action по url
        """
        ControllerCache.populate()

        matched = self._url_patterns.get(url)
        if matched:
            _, action = matched
            return action

    def get_top_actions(self):
        '''
        Получение списка действий или наборов, находящихся на первом уровне
        '''
        top_packs = []
        for stack, act in self._url_patterns.values():
            if len(stack) == 0:
                # значит экшин на верхнем уровне
                if act not in top_packs:
                    top_packs.append(act)
            else:
                if stack[0] not in top_packs:
                    top_packs.append(stack[0])
        return top_packs

    def get_packs(self):
        '''
        Возвращение всех паков в контроллере
        '''
        return list(self._packs_by_name.values())

    def reset(self):
        '''
        Сброс всего, что наделал контроллер с паками и экшенами
        '''
        for pack in self.top_level_packs:
            if hasattr(pack, '_built'):
                del(pack._built)
                for action in pack.actions:
                    if hasattr(action, '_built'):
                        del(action._built)
        self._actions_by_name.clear()
        self._actions_by_type.clear()
        self._packs_by_name.clear()
        self._packs_by_type.clear()
        self.top_level_packs = []

    @property
    def urlpattern(self):
        """
        Возвращает кортеж вида (pattern, method), пригодный для регистрации
        в urlpatterns Django
        """
        url = self.url
        if url.startswith('/'):
            url = url[1:]
        if url.endswith('/'):
            url = url[:-2]
        return (r'^%s/' % url, self.process_request)


class ControllerCache(object):
    '''
    Внутренний класс платформы,
    который отвечает за хранение кеша контроллеров
    и связанных с ним экшенов и паков.
    '''
    _loaded = False
    _write_lock = threading.RLock()

    # словарь зарегистрированных контроллеров в прикладном приложении
    _controllers = set()

    overrides = {}

    # =========================================================================
    # Методы, предназначенные для поиска экшенов
    # и паков во всех контроллерах системы
    # =========================================================================

    @classmethod
    def get_action_url(cls, type):
        """ Возвращает URL экшена *type* по его имени или классу """
        assert isinstance(type, string_types) or issubclass(type, Action)
        cls.populate()
        for cont in cls._controllers:
            url = cont.get_action_url(type)
            if url:
                return url

    @classmethod
    def find_pack(cls, pack):
        """
        Ищет заданный пак по имени класса или классу
        во всех зарегистрированных контроллерах.
        Возвращает экземпляр первого найденного пака.

        :param pack: имя класса или класс пака
        :type pack: строка формата package.Class или класс

        :return: экземпляр найденного пака
        :rtype: m3_core.actions.ActionPack
        """
        if inspect.isclass(pack):
            name = pack.get_short_name()
        elif isinstance(pack, string_types) and '.' in pack:
            name = pack
        else:
            raise ValueError(
                u'Pack must be a class or a string '
                u'like "package.subpackage.Class"'
            )

        cls.populate()
        over = cls.overrides.get(name)
        if over:
            return over
        else:
            for cont in list(cls._controllers):
                p = cont._find_pack(pack)
                if p:
                    return p

    @classmethod
    def find_action(cls, action):
        """
        Ищет заданный экшен по имени класса или классу
        во всех зарегистрированных контроллерах.
        Возвращает экземпляр первого найденного экшена.

        :param action: имя класса или класс экшена
        :type action: строка формата package.Class или класс

        :return: экземпляр найденного пака
        :rtype: m3_core.actions.Action
        """
        for cont in list(cls._controllers):
            p = cont._find_action(action)
            if p:
                return p

    @classmethod
    def get_action_by_url(cls, url):
        """ Возвращает Action по переданному *url* """
        for cont in cls._controllers:
            act = cont.get_action_by_url(url)
            if act:
                return act

    @classmethod
    def find_node_by_perm(cls, perm):
        """
        Возвращает экшн/пак по коду права, или None,
        если таковых не найдено
        :param string perm: код права
        :return: экземпляр найденного пара/экшна
        """
        for cont in cls._controllers:
            node = cont._find_node_by_perm(perm)
            if node:
                return node

    # =========================================================================
    @classmethod
    def _self_test(cls):
        """
        Проверяет корректность регистрации экшнов/паков
        """

        cls.populate()

        result = []

        def warn_if_class(obj, name):
            if inspect.isclass(obj):
                result.append(
                    '%r is not the instance but class!' % _name_of(obj))
                return True
            return False

        def shortname_checker(acc):
            def check(obj, name):
                sn = getattr(obj, 'shortname', None) or getattr(
                    obj, 'shortname', None)
                if sn:
                    name = acc.get(sn)
                    if name:
                        result.append(
                            '%r have shortname, reserved for %r!' % (
                                _name_of(obj), name))
                    else:
                        acc[sn] = _name_of(obj)
            return check

        def existense_checker(acc):
            def check(obj, name):
                if name in acc:
                    result.append(
                        '%r already registered!' % name)
                    return True
                acc.add(name)
                return False
            return check

        check_action_sn = shortname_checker({})
        check_pack_sn = shortname_checker({})
        check_reregister = existense_checker(set())

        def check_packs(packs):
            for pack in packs:
                pack_name = _name_of(pack)
                if not warn_if_class(pack, pack_name):
                    check_pack_sn(pack, pack_name)
                    if pack.subpacks:
                        result.append(
                            '%r have subpacks - code smell!' % pack_name)
                        check_packs(pack.subpacks)

                for action in pack.actions:
                    action_name = _name_of(action)
                    if not warn_if_class(action, action_name):
                        if not check_reregister(action, action_name):
                            if check_action_sn(action, action_name):
                                continue

        for cont in cls._controllers:
            check_packs(cont.top_level_packs)

        return result

    @classmethod
    def register_controller(cls, controller):
        '''
        Выполняет регистрацию контроллера *controller* во внутреннем кеше.
        '''
        assert isinstance(controller, ActionController)
        cls._controllers.add(controller)

    @classmethod
    def populate(cls):
        """
        Загружает в кэш ActionController'ы
        из перечисленных в INSTALLED_APPS приложений.
        В каждом из них загружает модуль *app_meta*
        и пытается выполнить метод *register_actions* внутри него.
        Выполняется только один раз. Возвращает истину в случае успеха.

        :return: флаг успешного завершения
        :rtype: boolean
        """
        if cls._loaded:
            return False
        cls._write_lock.acquire()
        try:
            if cls._loaded:
                return False

            cls.overrides = {}
            procs = []
            for app_name in get_installed_apps():
                try:
                    module = importlib.import_module('.app_meta', app_name)
                except ImportError as err:
                    if err.args[0].find('No module named') == -1:
                        raise
                    continue

                # расширение словаря overridde'ов
                for original_cls, new_inst in (
                    iteritems(getattr(module, 'action_pack_overrides', {}))
                ):
                    if inspect.isclass(original_cls):
                        original_cls = original_cls.get_short_name()
                    cls.overrides[original_cls] = new_inst

                # получение ф-ции - регистратора паков
                proc = getattr(module, 'register_actions', None)
                if callable(proc):
                    procs.append(proc)

            # собственно, регистрируем экшны
            for proc in procs:
                proc()

            cls._loaded = True
        finally:
            cls._write_lock.release()
        return True

    @classmethod
    def dump_urls(cls):
        '''
        Отладочный метод.
        Выводит в консоль адреса всех контроллеров зарегистрированных в кэше.
        '''
        print('------------ CONTROLLER CACHE DUMP ------------')
        for cont in cls._controllers:
            cont.dump_urls()

    @classmethod
    def require_update(cls):
        """
        Сбрасывает внутренний флаг заполненности контроллера.
        Следующий запрос к контроллеру вызовет
        перестройку иерархии экшенов и паков.
        """
        for cont in cls.get_controllers():
            cont.reset()
        cls._loaded = False

    @classmethod
    def get_controllers(cls):
        """
        Возвращает множество всех контроллеров зарегистрированных в кэше
        """
        return cls._controllers
