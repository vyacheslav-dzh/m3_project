# coding:utf-8
from __future__ import absolute_import

import threading

from six import iteritems
from six import itervalues
from six import with_metaclass


class CacheStat(object):
    '''
    Класс, хранящий статистику обращения к кешу
    '''
    def __init__(self):
        # число попаданий в закешированные данные
        self.in_cache = 0
        # число непопаданий в закешированные данные
        self.out_cache = 0
        # количество сбросов кеша с момента создания
        self.drops = 0
        # количество полных сбросов кеша с момента создания
        self.full_drops = 0


class RuntimeCacheMetaclass(type):
    '''
    Метакласс, который позволяет делать синглтоны, объединенные в иерархию, но
    при этом каждый уровень иерархии имеет свой shared_state.

    Метакласс замещает __init__ управляемого класса, в результате чего этот
    метод недоступен прикладным разработчикам. Для реализации логики
    инициализации класса можно реализовать метод custom_init(*args, **kwargs).
    '''
    def __new__(cls, name, bases, attrs):

        def default_init(self, *args, **kwargs):
            self.__dict__ = self._shared_state
            # добавляем возможность вызова кастомного инита (обычный уже
            # использован и не может быть реализован в дочерних к RuntimeCache
            # классах
            if hasattr(self, 'custom_init') and callable(self.custom_init):
                self.custom_init(*args, **kwargs)

            # в случае, если внутри класса кеша задан обработчик, то мы
            # пытаемся его зарегистировать
            if (
                hasattr(self, 'handler') and
                callable(self.handler) and
                not self.is_instance_registered()
            ):
                self.register_handler(self.handler)

        klass = super(RuntimeCacheMetaclass, cls).__new__(
            cls, name, bases, attrs
        )

        klass._shared_state = dict(
            # словарь Хэндлеров, с ключом в виде имени класса Хэндлера
            handlers={},
            handler_run_rules={},
            # собственно те данные, которые лежат в кеше
            data={},
            write_lock=threading.RLock(),
            stat=CacheStat(),
        )
        klass.__init__ = default_init

        return klass


class RuntimeCache(with_metaclass(RuntimeCacheMetaclass, object)):
    '''
    Класс, используемый для кеширования данных в рантайме приложения.

    Использование данного класса:
    '''

    def register_handler(self, handler):
        '''
        Регистрирует обработчик заполнения кешированных данных.
        '''
        # TODO: в качестве возможности будущего расширения функционала данного
        # класса можно предусмотреть возможность указания хендлеров сборки кеша
        # для отдельных значений измерений. Для хранения маппинга хендлеров
        # и измерений предусмотрен словарь handler_run_rules

        assert callable(handler), (
            u'Обработчик заполнения кеша должен быть callable'
        )

        # TODO: не совсем понятно, нужно ли ставить lock в данном случае
        try:
            self.write_lock.acquire()
            self.handlers[self.__class__.__name__] = handler
        finally:
            self.write_lock.release()

    def is_instance_registered(self):
        '''
        Проверяет, если ли в списке обработчиков кеша указанный хендлер
        '''
        return self.__class__.__name__ in self.handlers

    def _normalize_dimensions(self, dimensions):
        '''
        Выполняет нормализацию переданных измерений
        '''
        result = ()
        if dimensions:
            if isinstance(dimensions, tuple):
                result = dimensions
            else:
                result = (dimensions,)
        return result

    def _need_populate(self, cleaned_dims):
        '''
        Проверяет, нужно ли выполнять прогрузку кеша для указанных измерений
        '''
        return cleaned_dims not in self.data

    def _populate(self, dimensions):
        '''
        Метод собирает информацию по кешируемым объектам.

        Возвращает True, если populate реально состоялся
        '''
        dims = self._normalize_dimensions(dimensions)

        if not self._need_populate(dims):
            return False

        try:
            self.write_lock.acquire()
            if not self._need_populate(dims):
                return False
            for handler in itervalues(self.handlers):
                prepared_data = handler(self, dims)
                if isinstance(prepared_data, dict):
                    for key, value in iteritems(prepared_data):
                        self.set(key, value)
        finally:
            self.write_lock.release()

        return True

    def set(self, dimensions, value):
        '''
        Устанавливает значение в кеше
        '''
        self.data[self._normalize_dimensions(dimensions)] = value

    def get(self, dimensions, default=None):
        '''
        Возвращает значение из кеша
        '''
        dims = self._normalize_dimensions(dimensions)
        if self._populate(dims):
            self.stat.out_cache += 1
        else:
            self.stat.in_cache += 1
        return self.data.get(dims, default)

    def has_data(self, dimensions):
        '''
        Возвращает True в случае, если в кеше есть данные для указанных
        измерений
        '''
        dims = self._normalize_dimensions(dimensions)
        return dims in self.data

    def get_size(self):
        '''
        Возвращает количество объектов, помещенных в кеш
        '''
        return len(self.data)

    def drop(self, dimensions):
        '''
        Метод сброса кеша по измерению
        '''
        dims = self._normalize_dimensions(dimensions)

        try:
            self.write_lock.acquire()
            self.data.pop(dims, None)
            self.stat.full_drops += 1
        finally:
            self.write_lock.release()

    def drop_all(self):
        try:
            self.write_lock.acquire()
            self.data = {}
            self.stat.full_drops += 1
        finally:
            self.write_lock.release()

    def clear_stat(self):
        self.stat = CacheStat()

    def lock(self):
        '''
        Устанавливает блокировку на работу с внутренними данными
        '''
        self.write_lock.acquire()

    def unlock(self):
        '''
        Снимает блокировку на работу с внутренними данными
        '''
        self.write_lock.release()


class IntegralRuntimeCache(RuntimeCache):
    '''
    Кеш данных, которые собирает все данные однократно и не пересобирает их
    после этого. (пока не произойдет сброс кеша)
    '''
    def _need_populate(self, cleaned_dims):
        return not self.data


# =============================================================================
# Кеши, которые работают не как синглтоны. Их экземпляры имеют свое состояние.
# Данные кеши используются в процессе
# =============================================================================

class ObjectStorage(object):
    '''
    Класс кешей, объекты которых не являются синглтонами
    '''
    def __init__(self):
        self.data = {}
        self.handlers = []

        # в случае, если внутри класса кеша задан обработчик, то мы пытаемся
        # его зарегистировать
        if (
            hasattr(self, 'handler') and
            callable(self.handler) and
            not self.handler_registered(self.handler)
        ):
            self.register_handler(self.handler)

    def register_handler(self, handler):
        '''
        Регистрирует обработчик заполнения кешированных данных.
        '''
        # TODO: в качестве возможности будущего расширения функционала данного
        # класса можно предусмотреть возможность указания хендлеров сборки кеша
        # для отдельных значений измерений. Для хранения маппинга хендлеров
        # и измерений предусмотрен словарь handler_run_rules

        assert callable(handler), (
            u'Обработчик заполнения кеша должен быть callable'
        )

        # TODO: не совсем понятно, нужно ли ставить lock в данном случае
        if handler not in self.handlers:
            self.handlers.append(handler)

    def handler_registered(self, handler):
        '''
        Проверяет, если ли в списке обработчиков кеша указанный хендлер
        '''
        return handler in self.handlers

    def _normalize_dimensions(self, dimensions):
        '''
        Выполняет нормализацию переданных измерений
        '''
        result = ()
        if dimensions:
            if isinstance(dimensions, tuple):
                result = dimensions
            else:
                result = (dimensions,)
        return result

    def _need_populate(self, cleaned_dims):
        '''
        Проверяет, нужно ли выполнять прогрузку кеша для указанных измерений
        '''
        return cleaned_dims not in self.data

    def _populate(self, dimensions):
        '''
        Метод собирает информацию по кешируемым объектам.

        Возвращает True, если populate реально состоялся
        '''
        dims = self._normalize_dimensions(dimensions)

        if not self._need_populate(dims):
            return False

        if not self._need_populate(dims):
            return False
        for handler in self.handlers:
            prepared_data = handler(self, dims)
            if isinstance(prepared_data, dict):
                for key, value in iteritems(prepared_data):
                    self.set(key, value)

        return True

    def set(self, dimensions, value):
        '''
        Устанавливает значение в кеше
        '''
        self.data[self._normalize_dimensions(dimensions)] = value

    def get(self, dimensions, default=None):
        '''
        Возвращает значение из кеша
        '''
        dims = self._normalize_dimensions(dimensions)
        self._populate(dims)
        return self.data.get(dims, default)

    def has_data(self, dimensions):
        '''
        Возвращает True в случае, если в кеше есть данные для указанных
        измерений
        '''
        dims = self._normalize_dimensions(dimensions)
        return dims in self.data

    def get_size(self):
        '''
        Возвращает количество объектов, помещенных в кеш
        '''
        return len(self.data)

    def drop(self, dimensions):
        '''
        Метод сброса кеша по измерению
        '''
        dims = self._normalize_dimensions(dimensions)

        self.data.pop(dims, None)

    def drop_all(self):
        self.data = {}

    def clear_stat(self):
        self.stat = CacheStat()


class IntegralObjectStorage(ObjectStorage):
    '''
    Кеш данных, которые собирает все данные однократно и не пересобирает их
    после этого. (пока не произойдет сброс кеша)
    '''
    def _need_populate(self, cleaned_dims):
        return not self.data


class ModelObjectStorage(IntegralObjectStorage):
    '''
    Хранилище объектов, которые считывает все записи в моделях указанных типов.

    Ключом данных является идентификатор объекта.
    '''
    def __init__(self, model=None):
        super(ModelObjectStorage, self).__init__()
        self.model = model

    def handler(self, cache, dimensions):
        '''
        Обработчик кеша. Выполняется чтение всех данных из указанной в
        конструкторе модели.
        '''
        self.drop_all()
        if self.model:
            objects = self.model.objects.all()
            for object in objects:
                self.set(object.id, object)


# =============================================================================
# Класс "Фабрика объектных хранилищ"
# =============================================================================
class ModelObjectStorageFactory(object):
    '''
    Класс, отвечающий за организацию прозрачного интерфейса доступа к
    объектным хранилищам по различным типам моделей.

    Класс является thread-unsafe
    '''
    def __init__(self):
        self.storages = {}

    def get_storage(self, model):
        '''
        Получение объекта класса ModelObjectStorage для указанного типа модели.

        @param model: класс моделей, для которых выполняется построение
                      объектного хранилища.

        В случае, если значение параметр model не является классом наследником
        django.db.Model, то выбрасывается исключение TypeError
        '''
        if not model or not hasattr(model, 'objects'):
            raise TypeError(
                u'Для построения объектного хранилища ожидается '
                u'класс-наследник django.db.Model'
            )

        if model in self.storages:
            result = self.storages[model]
        else:
            result = ModelObjectStorage(model=model)
            self.storages[model] = result

        return result

    def drop(self):
        '''
        Очищает внутренние структуры фабрики
        '''
        for storage in self.storages.values():
            storage.drop_all()
