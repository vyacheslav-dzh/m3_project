# coding: utf-8
from __future__ import absolute_import

from importlib import import_module
from logging import getLogger
import threading

from django.conf import settings


logger = getLogger('django')


class ExtensionException(Exception):
    """
    Класс исключений для расширений
    """
    pass


class ExtensionPointDoesNotExist(ExtensionException):
    """
    Возникает в случае если не найдена точка расширения
    """
    def __init__(self, extension_name, *args, **kwargs):
        self.extension_name = extension_name

    def __str__(self):
        return 'ExtensionPoint "%s" not founded' % self.extension_name


class ExtensionPoint:
    """
    Класс, описывающий точку расширения с именем *name* и
    вызываемой по умолчанию функцией *default_listener*
    """
    def __init__(self, name='', default_listener=None):

        # название точки расширения
        # названия точек расширения необходимо выполять в форме
        # mis.core.schedules.get-busy-perions
        self.name = name
        # человеческое название точки расширения
        self.verbose_name = u'Точка расширения'
        # листенер, который добавляется по умолчанию
        self.default_listener = default_listener


class ExtensionHandler:
    """
    Класс-обертка над обработчиком точки расширения *handler*
    с именем *name* и режимом вызова *call_type*
    """
    # Константы, которые определяет порядок вызова листенера
    # точки расширения
    INSTEAD_OF_PARENT = 0
    BEFORE_PARENT = 1
    AFTER_PARENT = 2

    def __init__(self, handler=None, call_type=INSTEAD_OF_PARENT):
        self.handler = handler
        self.call_type = call_type


ExtensionListener = ExtensionHandler  # совместимость


class ExtensionManager:
    '''
    Класс, который управляет точками расширения приложений
    '''
    __shared_state = dict(
        loaded=False,
        # словарь точек расширения. ключом являются наименование точки
        # ExtensionPoint.name
        extensions={},
        # словарь листенер, которые выполняются для точек расширения
        # ключом является наименование точки расширения, значениями - список
        listeners={},
        # стек выполнения листенеров
        stack={},
        _write_lock=threading.RLock(),
    )

    def __init__(self):
        self.__dict__ = self.__shared_state

    def _populate(self):
        '''
        Метод собирает точки расширения из
        app_meta приложений
        '''
        if self.loaded:
            return False
        self._write_lock.acquire()
        try:
            if self.loaded:
                return False
            for app_name in settings.INSTALLED_APPS:
                try:
                    module = import_module('.app_meta', app_name)
                except ImportError as err:
                    if err.args[0].find('No module named') == -1:
                        raise
                    continue
                proc = getattr(module, 'register_extensions', None)
                if callable(proc):
                    proc()
            self.loaded = True
        finally:
            self._write_lock.release()

    def _validate_extension_point(self, extension_point):
        '''
        Проверяет точку расширения на возможность регистрации
        в менеджере
        '''
        return (
            extension_point and
            isinstance(extension_point, ExtensionPoint) and
            extension_point.name and
            extension_point.name.strip() and
            isinstance(extension_point.default_listener, ExtensionListener) and
            extension_point.name not in self.extensions
        )

    def register_point(self, extension_point):
        '''
        Добавляет точку расширения
        '''
        if not self._validate_extension_point(extension_point):
            return

        point_key = extension_point.name.strip()
        self.extensions[point_key] = extension_point
        self.listeners[point_key] = [extension_point.default_listener]

    append_point = register_point  # для совместимости

    def register_point_external(self, extension_point):
        '''
        Метод регистрации точки расширения, который должен использоваться
        извне.

        Данный метод отличается от register_point тем, что при его
        использовании выставляются внутренние локи, что потенциально
        может привести к падению производительности системы.

        Поэтому, если не лень, используйте не декоратор @extension_point,
        а пишите определение точки расширения в app_meta. Да прибудет с вами
        сила, чтоле.
        '''
        if not self._validate_extension_point(extension_point):
            return

        self._write_lock.acquire()
        try:
            self.register_point(extension_point)
        except:  # noqa
            logger.exception(
                u"Не удалось зарегистрировать точку расширения '%s'"
                .format(extension_point.name)
            )
        finally:
            self._write_lock.release()

    def check_point_exists(self, extension_point_name):
        '''
        Проверяет, существует ли во внутреннем кеше определение
        точки расширения с указанным именем
        '''
        return extension_point_name in self.extensions

    def register_handler(self, extension_name, listener):
        '''
        Добавляет листенер точки расширения с именем extension_name
        '''
        if (
            not listener or
            not isinstance(listener, ExtensionListener) or
            not listener.handler
        ):
                # передали неправильное определение листенера
                # ничего не делаем
                return
        try:
            self.listeners[extension_name].append(listener)
        except KeyError:
            raise ExtensionPointDoesNotExist(extension_name=extension_name)

    append_listener = register_handler  # для совместимости

    def execute(self, extension_name, *args, **kwargs):
        '''
        Выполняет связанное с точкой расширения текущее действие
        '''
        result = None
        if not self.loaded:
            self._populate()

        if (
            extension_name not in self.extensions or
            extension_name not in self.listeners
        ):
            return None

        if (
            extension_name not in self.stack or
            not self.stack[extension_name]
        ):
            # необходимо выполнить подготовку стека вызовов
            listener_stack = []
            if (
                len(self.listeners[extension_name]) == 1 and
                not self.listeners[extension_name]
            ):
                # обработка случая, когда в качестве дефолтного листенера задан
                # пустой обработчик и больше обработчиков нет
                listener_stack = [None]
            else:
                for listener in self.listeners[extension_name]:
                    if not listener or not listener.handler:
                        continue
                    INSTEAD_OF_PARENT = ExtensionListener.INSTEAD_OF_PARENT
                    if listener.call_type == INSTEAD_OF_PARENT:
                        listener_stack = [listener]
                    elif listener.call_type == ExtensionListener.BEFORE_PARENT:
                        listener_stack.insert(0, listener)
                    else:
                        listener_stack.append(listener)

            self.stack[extension_name] = listener_stack

        # собственно, выполняем точки расширения
        for listener in self.stack[extension_name]:
            kwargs['ext_result'] = result
            if listener and callable(listener.handler):
                result = listener.handler(*args, **kwargs)

        return result

    def get_handlers(self, extension_name):
        '''
        Возвращает список хендлеров, которые объявлены для указанной точки
        расширения.

        Хендлеры возвращаются списком. Элементы данного списка идут в порядке
        их регистрации
        '''
        self._populate()
        return self.listeners.get(extension_name, [])


# =============================================================================
# Декораторы для работы с точками расширения
# =============================================================================
def extension_point(name=''):
    '''
    Декортатор, с помощью которого определяется точка расширения c именем
    *name*.

    Данный декоратор должен использоваться над дефолтным хендлером
    точки расширения с указанным именем name
    '''
    def inner(f):
        def wrapper(*args, **kwargs):
            return ExtensionManager().execute(name, *args, **kwargs)
        # формируем определение точки расширения
        if not ExtensionManager().check_point_exists(name):
            # пытаемся добавить точку расширения
            ExtensionManager().register_point_external(
                ExtensionPoint(
                    name=name,
                    default_listener=ExtensionHandler(f)
                )
            )
        return wrapper
    return inner
