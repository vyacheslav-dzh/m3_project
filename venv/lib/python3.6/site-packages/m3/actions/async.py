# coding:utf-8
u"""Экшены для работы в асинхронном режиме."""
from __future__ import absolute_import

from abc import ABCMeta
from abc import abstractmethod
from logging import getLogger
from threading import Thread
import json

from django import http
from m3.actions import ACD
from m3.actions import Action
from m3.actions.results import ActionResult
from m3_django_compat import get_installed_apps
from six import with_metaclass


logger = getLogger('django')


# !!!!!
# для работы данного модуля необходимо наличие приложения 'm3_mutex'
# в INSTALLED_APPS приложения

# TODO: переделать по правильному - платформа не должна импортировать
# модули контрибов

if 'm3_mutex' not in get_installed_apps():
    # При сборке документации внешняя Django ничего не знает про m3_mutex
    logger.warning(
        u'For working async operations "m3_mutex" '
        u'must be define in INSTALLED_APPS')
    # raise ImportError(
    # u'For working async operations "m3_mutex" must'
    # ' be define in INSTALLED_APPS')

try:
    from m3_mutex import (
        capture_mutex, release_mutex, request_mutex,
        MutexID, MutexOwner, MutexBusy, MutexState, TimeoutAutoRelease)
except ImportError:
    logger.warning(u'm3_mutex import error')


class IBackgroundWorker(with_metaclass(ABCMeta, Thread)):
    '''
    Абстрактный класс для исполнения кода в фоновом режиме.
    Соответсвующие методы должны быть определены разработчиком.
    Тк класс представляет собой наследник Thread, нужно понимать
    что фактически при использовании будет создан новый тред,
    и учитывать это при доступе к ресурсам приложения, кои могут
    быть модифицированы другими тредами. Методы stop, start и ping
    должны возвращать экземпляры  AsyncOperationResult.
    Вообще говоря, можно реализовать данный класс подругому,
    главное чтобы был имплементирован интерфейс
    (например если есть нужда использовать мутекс на уровне
    нескольких инстансов приложений)
    '''

    def __init__(self, boundary='', context=None, *args, **kwargs):
        super(IBackgroundWorker, self).__init__()
        self.boundary = boundary
        self.context = context

    def start(self):
        '''
        Запускает исполнение кода. Метод не абстрактный, при
        замещении обязательно вызывать суперкласс
        '''
        super(IBackgroundWorker, self).start()

    @abstractmethod
    def run(self):
        '''
        Метод вызывается после вызоыва метода start() в новом потоке,
        здесь фактически следует писать собственный код
        '''
        raise NotImplementedError(
            'Method run() must be overrided in  child class')

    @abstractmethod
    def stop(self):
        '''
        Метод остановки операции
        '''
        raise NotImplementedError(
            'Method stop() must be overrided in  child class')

    @abstractmethod
    def request(self):
        '''
        Запрос состояния операции
        '''
        raise NotImplementedError(
            'Method request() must be overrided in  child class')

    @abstractmethod
    def result(self):
        '''
        Запрос на получение результата асинхронной операции
        '''
        raise NotImplementedError(
            'Method result() must be overrided in  child class')

    # ------------------------------------------------------------------------
    # Методы работы c глобальными блокировками операции с указанным инстансом.
    # В базовом классе глобальная блокировка реализована с помощью семафоров
    # ------------------------------------------------------------------------
    MUTEX_GROUP = 'asyncop'
    RESULT_MUTEX_GROUP = 'asyncop-result'

    MUTEX_TIMEOUT = 600
    RESULT_MUTEX_TIMEOUT = 300

    def _mutex_id(self):
        return MutexID(group=IBackgroundWorker.MUTEX_GROUP, id=self.boundary)

    def _mutex_owner(self):
        return MutexOwner(
            name=IBackgroundWorker.MUTEX_GROUP, session_id=self.boundary)

    def _result_mutex_id(self):
        return MutexID(
            group=IBackgroundWorker.RESULT_MUTEX_GROUP, id=self.boundary)

    def _result_mutex_owner(self):
        return MutexOwner(
            name=IBackgroundWorker.RESULT_MUTEX_GROUP,
            session_id=self.boundary)

    def lock(self):
        '''
        Устанавливает глобальную блокировку по инстансу с использованием
        механизма мютексов
        '''
        try:
            capture_mutex(
                mutex_id=self._mutex_id(), owner=self._mutex_owner(),
                auto_release=TimeoutAutoRelease(timeout=self.MUTEX_TIMEOUT))
        except MutexBusy:
            raise Exception(
                u'asyncop: Не удалось установить'
                u' глобальную блокировку операции.')

    def unlock(self):
        '''
        Освобождает глобальную блокировку состояния
        '''
        release_mutex(mutex_id=self._mutex_id(), owner=self._mutex_owner())

    def check_state(self):
        '''
        Проверяет состояние глобальной блокировки операции.
        Возвращает кортеж из двух элементов (is_active, status_data),
        где is_active=True/False - показывает активность
        установленной блокировки, и status_data - произвольный
        объект (чаще строка), в котором
        находится описание состоания операции.
        '''
        mutex_state, mutex = request_mutex(self._mutex_id())
        return (
            (mutex_state != MutexState.FREE, mutex.status_data)
            if mutex is not None else '')

    def refresh_state(self, status_data):
        '''
        Обновляет состояние блокировки
        '''
        try:
            capture_mutex(
                mutex_id=self._mutex_id(), owner=self._mutex_owner(),
                status_data=status_data,
                auto_release=TimeoutAutoRelease(timeout=self.MUTEX_TIMEOUT))
        except MutexBusy:
            raise Exception(
                u'asyncop: Не удалось установить'
                u' глобальную блокировку операции.')

    def lock_result(self, result):
        '''
        Блокирует результат выполнения операции
        '''
        try:
            capture_mutex(
                mutex_id=self._result_mutex_id(),
                owner=self._result_mutex_owner(),
                auto_release=TimeoutAutoRelease(
                    timeout=self.RESULT_MUTEX_TIMEOUT),
                status_data=result or '')
        except MutexBusy:
            raise Exception(
                u'asyncop: Не удалось сохранить'
                u' результат выполнения оajyjперации.')

    def unlock_result(self):
        release_mutex(
            mutex_id=self._result_mutex_id(),
            owner=self._result_mutex_owner(),)

    def request_result(self):
        mutex_state, mutex = request_mutex(self._result_mutex_id())
        return (
            (mutex_state != MutexState.FREE, mutex.status_data)
            if mutex is not None else '')


class AsyncOperationResult(ActionResult):
    '''
    Результат выполнения асинхронной операции.
    '''

    def __init__(self, value=0.0, text='', alive=True):
        self.value = value
        self.text = text
        self.alive = alive

    def get_http_response(self):
        data = json.dumps({
            'text': self.text,
            'value': self.value,
            'alive': self.alive,
        })
        return http.HttpResponse(data, content_type='application/json')


class AsyncAction(Action):
    '''
    Экшен обработки запросов с клиента. В данном варианте инстанс
    класса воркера общий для всех http запросов экземпляра приложения,
    поэтому если один пользователь начал фоновую операцию, все пользователи
    этого сервера будут видеть такой же прогресс. Можно с помощью словаря
    сессий, где бы хранились инстансы воркеров, реализовать для каждого
    пользователя свою операцию, или же с помощью мутексов глобальную
    блокировку операции на уровне приложения. При наследовании должен
    быть определен атрибут worker_cls - класс
    наследующий/имплементирующий IBackgroundWorker
    '''
    COMMAND_START = 'start'
    COMMAND_STOP = 'stop'
    COMMAND_REQUEST = 'request'
    COMMAND_RESULT = 'result'

    worker_cls = None

    def __init__(self):
        super(AsyncAction, self).__init__()

    def context_declaration(self):
        return [
            ACD(name='command', type=str, required=True),
            ACD(name='boundary', type=str, required=True,
                default='default-boundary'), ]

    def _worker_instance(self, request, context, *args, **kwargs):
        '''
        Метод, возвращающий экземпляр воркера фоновой операции.
        Данный метод можно переопределять для случаев, когда в
        воркер необходимо передавать дополнительные параметры.
        '''
        return self.worker_cls(boundary=context.boundary, context=context)

    def run(self, request, context):

        if self.worker_cls is None:
            raise NotImplementedError('Worker class is not defined')

        if context.command == self.COMMAND_START:
            return self.start_operation(request, context)
        elif context.command == self.COMMAND_STOP:
            return self.stop_operation(request, context)
        elif context.command == self.COMMAND_REQUEST:
            return self.request_operation(request, context)
        elif context.command == self.COMMAND_RESULT:
            return self.result_operation(request, context)

    def start_operation(self, request, context):
        worker_instance = self._worker_instance(request, context)
        # если операция уже запущенна, то перезапустим
        # надо подумать насколько корректно такое поведение
        worker_instance.stop()

        return worker_instance.start()

    def stop_operation(self, request, context):
        return self._worker_instance(request, context).stop()

    def request_operation(self, request, context):
        return self._worker_instance(request, context).request()

    def result_operation(self, request, context):
        return self._worker_instance(request, context).result()
