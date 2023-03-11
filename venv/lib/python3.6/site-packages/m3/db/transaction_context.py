# coding: utf-8
"""
Модуль позволяющий реализовать управление порядком выполннения
обработчиков сигналов возбужденных в контексте некоей операции,
"завернутой" в транзакцию.

Типичный сценарий:

# сигнал на сохранение объекта пользователя
@receiver(post_save, sender=User)
@delay_in_situations('user_import', 'user_creation')
def save_user(model, obj, created, *args):
    if created:
        data = {'username': obj.username}
        def send_email():
            print "Sending EMail...."
            print data
    else:
        def send_mail():
            pass
    return send_email

...

# где-то в коде импорта пользователей
with TransactionCM('user_import'):
    for login, pwd in source:
        with SavePointCM():
            user = User(username=login)
            user.set_password(pwd)
            user.save()
"""

from __future__ import absolute_import

from threading import local as _local
from uuid import uuid4

from django.db import transaction


_state = _local()


def _reset_state():
    _state.rolled_back = set()
    _state.task_queue = []
    _state.cookies = []
    _state.situation = None


_reset_state()


class AbortTransaction(Exception):
    """
    Исключение, прерывающее текущий CM (SavePointCM/TransactionCM).
    Поймав это исключение CM откатывает изменения,
    произошедшие с момента входа в него и корректно завершается.
    """
    pass


class SavePointCM(object):
    """
    Context Manager создающий на время своего действия savepoint
    """

    def __init__(self):
        assert _state.situation, (
            u'Must be nested in TransactionCM!'
        )
        cookie = uuid4()
        _state.cookies.append(cookie)
        self._cookie = cookie
        self._sid = None

    def __enter__(self):
        self._sid = transaction.savepoint()
        return self

    def __exit__(self, ex_type, ex_inst, traceback):
        _state.cookies.remove(self._cookie)
        if ex_type is None:
            transaction.savepoint_commit(self._sid)
            return True
        else:
            transaction.savepoint_rollback(self._sid)
            _state.rolled_back.add(self._cookie)
            return (ex_type is AbortTransaction)

    def rollback(self):
        raise AbortTransaction()


class TransactionCM(object):
    """
    ContextManager создающий "ручную" транзакцию
    """
    def __init__(self, situation, rollback_all=True, using=None):
        assert not _state.situation, (
            u'Nested contexts not supported!'
        )
        _reset_state()
        _state.situation = self._situation = situation

        self._using = using

    def __enter__(self):
        transaction.enter_transaction_management(True, self._using)
        return self

    def __exit__(self, ex_type, ex_inst, traceback):
        if ex_type is None:
            transaction.commit(self._using)
            for (task, cookie) in _state.task_queue:
                if cookie not in _state.rolled_back:
                    task()
            _reset_state()
            return True
        else:
            transaction.rollback(self._using)
            _reset_state()
            return (ex_type is AbortTransaction)

    def rollback(self):
        raise AbortTransaction()


def delay_in_situations(*situations):
    """
    Оборачивает функцию и возвращает функцию-обработчик сигнала Django.
    В контексте одной из ситуаций, перечисленных в @situations
    выполнение функции, возвращённой оборачиваемой функцией,
    будет отложено до успешного завершения TransactionCM, создавшего ситуацию.
    """
    def wrapper(fn):
        def inner(*args, **kwargs):
            cont = fn(*args, **kwargs)
            assert callable(cont), "Task must return the callable object!"
            situation = _state.situation
            if not situation or situation not in situations:
                cont()
            else:
                cookie = (_state.cookies or [None])[-1]
                _state.task_queue.append((cont, cookie))
        return inner
    return wrapper
