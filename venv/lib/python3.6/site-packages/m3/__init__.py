# coding: utf-8
u"""Платформа разработки приложений ERP типа на python и django."""
from __future__ import absolute_import

from functools import reduce
from json.encoder import encode_basestring
from json.encoder import encode_basestring_ascii
import copy
import datetime
import decimal
import json
import sys

from django.conf import settings
from django.contrib import auth
from django.db import models as dj_models
from django.http import HttpResponseRedirect
from django.http import HttpResponseServerError
from django.utils import datetime_safe
from django.views.debug import ExceptionReporter
from m3_django_compat import ModelOptions
from m3_django_compat import is_authenticated
from m3_django_compat.middleware import MiddlewareMixin

from .actions import ApplicationLogicException
from .actions import OperationResult
from .actions.urls import get_app_urlpatterns


def escape_html(s):
    u"""Экранирует символы HTML разметки.

    :param  basestring s: строка для экранирования

    :rtype: basestring
    """
    replace_map = (
        ('&', '&amp;'),
        ('<', '&lt;'),
        ('>', '&gt;'),
    )
    return reduce(lambda s, r: s.replace(*r), replace_map, s)


def _encode_basestring(s):
    """Возвращает представление Python строк для JSON.

    Расширяет функцию :func:`json.encoder.encode_basestring` поддержкой
    строк, помеченных с помощью :func:`~django.utils.safestring.mark_safe`.
    """
    for_escape = not hasattr(s, '__html__')
    result = encode_basestring(s)
    if for_escape:
        result = escape_html(result)
    return result


def _encode_basestring_ascii(s):
    """Возвращает только ASCII представление Python строк для JSON .

    Расширяет функцию :func:`json.encoder.encode_basestring_ascii` поддержкой
    строк, помеченных с помощью :func:`~django.utils.safestring.mark_safe`.
    """
    for_escape = not hasattr(s, '__html__')
    result = encode_basestring_ascii(s)
    if for_escape:
        result = escape_html(result)
    return result


def date2str(date, template=None):
    """
    datetime.strftime глючит с годом < 1900
    типа обходной маневр (взято из django)
    WARNING from django:
    # This library does not support strftime's \"%s\" or \"%y\" format strings.
    # Allowed if there's an even number of \"%\"s because they are escaped.
    """
    default_format = getattr(settings, 'PYTHON_DATE_FORMAT', '%d.%m.%Y')
    return datetime_safe.new_datetime(date).strftime(
        template or default_format
    )


class AutoLogout(object):
    '''
    Отслеживает активность пользователей в системе.
    Если с последнего действия пользователя прошло времени
    больше чем INACTIVE_SESSION_LIFETIME,
    то он выводит пользователя из системы
    '''

    session_key = 'app_last_user_activity_time'

    def process_request(self, request):
        # Если проверка отключена
        if settings.INACTIVE_SESSION_LIFETIME == 0:
            return

        # У аутентифицированного пользователя проверяем таймаут,
        # а ананимусов сразу посылаем
        if is_authenticated(request.user):
            last_time = request.session.get(self.session_key, None)
            if last_time is not None:
                delta = datetime.datetime.now() - last_time
                if delta.seconds // 60 > settings.INACTIVE_SESSION_LIFETIME:
                    # После логаута сессия уже другая
                    # и присваивать время не нужно
                    auth.logout(request)
                    return

            # Записываем время последнего запроса
            request.session[self.session_key] = datetime.datetime.now()


class _EncodeFunctionsPatcher(object):
    u"""Менеджер контекста для подмены кодировщиков в ``M3JSONEncoder``

    Поскольку функций кодировщиков нет в самом инстансе класса
    ``M3JSONEncoder`` чтобы не переписывать родительский метод с заменой 2
    переменных заменяем их с помощью менеджера контекста через глобальные
    переменные метода iterencode на содержащие возможность экранирования HTML
    функции, после использования возвращаем обратно.
    """

    def __init__(self, encoder):
        self.encoder = encoder
        self._globals = self.encoder.iterencode.__func__.__globals__

    def __enter__(self):
        self._globals['encode_basestring'] = _encode_basestring
        self._globals['encode_basestring_ascii'] = _encode_basestring_ascii

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._globals['encode_basestring'] = encode_basestring
        self._globals['encode_basestring_ascii'] = encode_basestring_ascii


class M3JSONEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        self.dict_list = kwargs.pop('dict_list', None)
        super(M3JSONEncoder, self).__init__(*args, **kwargs)

    def iterencode(self, *args, **kwargs):
        # передаем через super, т.к. нам нужны переменные родительского класса
        with _EncodeFunctionsPatcher(super(M3JSONEncoder, self)):
            return super(M3JSONEncoder, self).iterencode(*args, **kwargs)

    def default(self, obj):
        # обработаем простейшие объекты,
        # которые не обрабатываются стандартным способом
        if isinstance(obj, datetime.datetime):
            return '%02d.%02d.%04d %02d:%02d:%02d' % (
                obj.day, obj.month, obj.year, obj.hour, obj.minute, obj.second)
        elif isinstance(obj, datetime.date):
            return '%02d.%02d.%04d' % (obj.day, obj.month, obj.year)
        elif isinstance(obj, datetime.time):
            return obj.strftime('%H:%M')
        elif isinstance(obj, decimal.Decimal):
            return str(obj)

        # Прошерстим методы и свойства, найдем те,
        # которые могут передаваться на клиента
        # Клонирование словаря происходит потому,
        # что сериализуемые методы переопределяются результатами своей работы
        cleaned_dict = {}
        dict = copy.copy(obj.__dict__)

        # Для джанговских моделей функция dir
        # дополнительно возвращает "ссылки" на связанные модели.
        # Их не нужно сериализовать, а также при обращении к ним
        # происходят запросы к БД. Причем на практике есть случаи,
        # когда эти запросы вызвают эксепешны
        # (например, если изменен id'шник объекта)
        related_objs_attrs = []
        manager_names = []
        if isinstance(obj, dj_models.Model):
            related_objs = ModelOptions(obj).get_all_related_objects()
            related_objs_attrs = [ro.model_name for ro in related_objs]
            # Также соберем все атрибуты-менеджеры (их может быть несколько).
            # Сюда попадет "objects", который исключаем из обработки ниже.
            for attr in obj.__class__.__dict__:
                if isinstance(
                    obj.__class__.__dict__[attr],
                    dj_models.manager.ManagerDescriptor
                ):
                    manager_names.append(attr)

        # если передали специальный список атрибутов, то пройдемся по ним
        # атрибуты вложенных объектов разделены точкой
        # будут созданы вложенные объекты для кодирования
        if self.dict_list:
            for item in self.dict_list:
                lst = item.split('.')
                value = obj
                arr = dict
                last_attr = None
                set_value = False
                for attr in lst:
                    if last_attr:
                        if last_attr not in arr:
                            arr[last_attr] = {}
                        else:
                            if not isinstance(arr[last_attr], type({})):
                                value = None
                                # у объекта уже стоит свойство не словарь,
                                # видимо оно пришло откуда-то свыше
                                set_value = False
                                break
                        arr = arr[last_attr]
                    if hasattr(value, attr):
                        value = getattr(value, attr)
                        if callable(value):
                            # это не свойство а функция, вызовем ее
                            value = value()
                        # нашли свойство, значит его надо будет
                        # поставить после цикла
                        set_value = True
                    else:
                        value = None
                    last_attr = attr
                if set_value:
                    arr[attr] = value

        for attr in dir(obj):
            # Во всех экземплярах моделей Django есть атрибут "objects",
            # т.к. он является статик-атрибутом модели.
            # Но заботливые разработчики джанги позаботились о нас
            # и выкидывают спицифичную ошибку
            # "Manager isn't accessible via %s instances"
            # при обращении из экземпляра.
            # Поэтому "objects" нужно игнорировать.
            # Да и вообще все менеджеры надо игнорировать,
            # т.к. их имена мы собираем выше.
            # Также проигнорируем приватные и протектнутные атрибуты
            if (
                not attr.startswith('_')
                and attr not in manager_names
                and attr != 'tree'
                and attr not in related_objs_attrs
            ):
                try:
                    # если метод или свойство есть в классе,
                    # то проверим у него признак
                    class_attr_value = getattr(obj.__class__, attr, None)
                    if class_attr_value is not None:
                        json_encode = getattr(
                            class_attr_value, 'json_encode', False)
                        if json_encode:
                            value = getattr(obj, attr)
                            if callable(value):
                                # если это метод, то вызовем его
                                dict[attr] = value()
                            else:
                                # иначе это было свойство или какой-то атрибут
                                dict[attr] = value
                except Exception as exc:
                    # Вторая проблема с моделями в том,
                    # что dir кроме фактических полей возвращает ассессоры.
                    # При попытке обратиться к ним происходит запрос(!)
                    # и может возникнуть ошибка DoesNotExist
                    # Заботливые разработчики Django
                    # сделали её разной для всех моделей ;)
                    if exc.__class__.__name__.find('DoesNotExist') == -1:
                        raise

        for attribute in dict.keys():
            # Для полей типа myfield_id автоматически создается атрибут,
            # ссылающияся на наименование, например,
            # для myfield_id будет myfield_ref_name,
            # конечно если у модели myfield есть name.
            # Зачем это нужно - х.з.
            if len(attribute) > 3 and attribute.endswith('_id'):
                try:
                    field_name = attribute[0:len(attribute) - 3]
                    if getattr(getattr(obj, field_name), 'name'):
                        if callable(getattr(getattr(obj, field_name), 'name')):
                            cleaned_dict[field_name + '_ref_name'] = getattr(
                                getattr(obj, field_name), 'name')()
                        else:
                            cleaned_dict[field_name + '_ref_name'] = getattr(
                                getattr(obj, field_name), 'name')
                except:  # noqa
                    pass
            if len(attribute) > 6 and attribute.endswith('_cache'):
                # вережим этот кусок, т.к. если есть кэш на ForeignKey,
                # то он отработался на верхнем этапе
                # а если кэш на что-то другое (set etc),
                # то фиг знает какое свойство у него надо брать
                pass
            # Ибо нефиг сериализовать protected/private атрибуты!
            if attribute.startswith('_'):
                pass
            else:
                # просто передадим значение,
                # оно будет закодировано в дальнейшем
                cleaned_dict[attribute] = dict[attribute]
        return cleaned_dict


def json_encode(f):
    """
    Декоратор, которым нужно отмечать сериализуемые в M3JSONEncoder методы
    """
    f.json_encode = True
    return f


class property_json_encode(property):
    """
    Декоратор для свойств, которые нужно отмечать сериализуемые в M3JSONEncoder
    """
    json_encode = True


class RelatedError(Exception):
    """
    Исключение для получения связанных объектов
    """
    pass


def authenticated_user_required(f):
    """
    Декоратор проверки того, что к обращение к требуемому ресурсу системы
    производится аутентифицированным пользователем
    """

    def action(request, *args, **kwargs):
        user = request.user
        if not user or not is_authenticated(user):
            if request.is_ajax():
                res = OperationResult.by_message(
                    u'Вы не авторизованы. Возможно, закончилось время '
                    u'пользовательской сессии.<br>'
                    u'Для повторной аутентификации обновите страницу.')
                return res.get_http_response()
            else:
                return HttpResponseRedirect('/')
        else:
            return f(request, *args, **kwargs)

    return action


class PrettyTracebackMiddleware(MiddlewareMixin):
    """
    Middleware, выводящая traceback'и в html-виде
    """
    def process_exception(self, request, exception):
        reporter = ExceptionReporter(request, *sys.exc_info())
        html = reporter.get_traceback_html()
        return HttpResponseServerError(html, content_type='text/html')
