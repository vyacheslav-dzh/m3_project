# coding:utf-8
u"""Модуль, реализущий работу с контекстом выполнения операции."""
from __future__ import absolute_import

from decimal import Decimal
from logging import getLogger
import datetime
import json

from django.utils.encoding import force_text
from m3_django_compat import get_request_params
from six import iteritems
from six import python_2_unicode_compatible
from six import string_types
from six import text_type


logger = getLogger('django')


def _date2str(*args):
    # lazy import
    from m3 import date2str
    return date2str(*args)


# ============================= ИСКЛЮЧЕНИЯ ====================================
class ActionContextException(Exception):
    """ Базовый класс для исключений контекста """
    pass


class RequiredFailed(ActionContextException):
    u'''
    Исключительная ситуация, которая выбрасывается в случае
    если фактическое наполнение контекста действия не соответствует
    описанным правилам
    '''
    def __init__(self, reason):
        self.reason = reason


class ConversionFailed(ActionContextException):
    u"""
    Исключение, которое выбрасывается,
    если значение из запроса *value*
    не удалось привести к типу *type*,
    указанному в правиле ActionContextDeclaration
    """
    def __init__(self, value, type, *args, **kwargs):
        super(ConversionFailed, self).__init__(*args, **kwargs)
        self.value = value
        self.type = type

    def __str__(self):
        return u'Can not convert value of "%s" in a given type "%s"' % (
            self.value, self.type)


@python_2_unicode_compatible
class ContextBuildingError(ActionContextException):
    u"""
    Ошибка построения контекста
    """
    def __init__(self, requiremets=None, errors=None):
        u"""
        :param unicode requiremets: обязательные параметры контекста,
            отсутствующие в запросе
        :param unicode errors: параметры, значение которых
            не удалось распарсить
        """
        assert requiremets or errors, (
            "requiremets or errors must be provided!")
        # параметры должны быть либо в "ошибках", либо в "недостающих"
        assert not set(errors).intersection(requiremets), (
            "requiremets can't contain errors!")
        self.errors = errors or []
        self.requiremets = requiremets or []

    def __repr__(self):
        return "%s(requiremets=%r, errors=%r)" % (
            self.__class__.__name__,
            self.requiremets,
            self.errors
        )

    __str__ = __repr__

    def __str__(self):
        log = []
        for title, data in (
            (u"Отсутствуют обязательные параметры:", self.requiremets),
            (u"Неверно заполнены параметры:", self.errors)
        ):
            if data:
                log.append(title)
                log.extend([("- %s" % d) for d in data])
        return u"\n".join(log)


class CriticalContextBuildingError(ContextBuildingError):
    u"""
    Критическая ошибка построения контекста
    """
    pass


# ================================== ПАРСЕРЫ ==================================
def _make_datetime_parser(extractor, formats):
    def parse(value):
        for (fmt, length) in formats:
            try:
                result = extractor(
                    datetime.datetime.strptime(value[:length], fmt)
                )
                break
            except ValueError:
                pass
        else:
            raise AssertionError()
        return result
    return parse


def _make_simple_parser(result_type, exceptions=(ValueError, TypeError)):
    def parse(value):
        try:
            result = result_type(value)
        except exceptions:
            raise AssertionError()
        return result
    return parse


def _object_parser(value):
    try:
        result = json.loads(value)
    except ValueError:
        raise AssertionError()
    return result


def _bool_parser(value):
    return value in ('true', 'True', 1, '1', 'on', True)


_datetime_parser = _make_datetime_parser(lambda x: x, formats=(
    ('%Y-%m-%dT%H:%M:%S', 19),
    ('%Y-%m-%d %H:%M:%S', 19),
    ('%d.%m.%Y %H:%M:%S', 19),
    ('%Y-%m-%dT%H:%M', 16),
    ('%Y-%m-%d %H:%M', 16),
    ('%d.%m.%Y %H:%M', 16),
    ('%Y-%m-%d', 10),
    ('%d.%m.%Y', 10),
    ('%H:%M:%S', 8),
    ('%H:%M', 5),
))


_date_parser = _make_datetime_parser(
    lambda x: x.date(),
    formats=(
        ('%Y-%m-%d', 10),
        ('%d.%m.%Y', 10),
        ('%m/%d/%Y', 10),
    ))


_time_parser = _make_datetime_parser(
    lambda x: x.time(),
    formats=(
        ('%H:%M:%S', 8),
        ('%H:%M', 5),
    ))


_PARSERS = {
    str: _make_simple_parser(text_type),  # Иду на поводу у хомячков (FIXME)
    text_type: _make_simple_parser(text_type),
    int: _make_simple_parser(int),
    float: _make_simple_parser(float),
    Decimal: _make_simple_parser(Decimal),

    object: _object_parser,

    bool: _bool_parser,

    datetime.datetime: _make_datetime_parser(lambda x: x, formats=(
        ('%Y-%m-%dT%H:%M:%S', 19),
        ('%Y-%m-%d %H:%M:%S', 19),
        ('%d.%m.%Y %H:%M:%S', 19),
        ('%Y-%m-%dT%H:%M', 16),
        ('%Y-%m-%d %H:%M', 16),
        ('%d.%m.%Y %H:%M', 16),
        ('%Y-%m-%d', 10),
        ('%d.%m.%Y', 10),
        ('%H:%M:%S', 8),
        ('%H:%M', 5),
    )),

    datetime.date: _make_datetime_parser(lambda x: x.date(), formats=(
        ('%Y-%m-%d', 10),
        ('%d.%m.%Y', 10),
        ('%m/%d/%Y', 10),
    )),

    datetime.time: _make_datetime_parser(lambda x: x.time(), formats=(
        ('%H:%M:%S', 8),
        ('%H:%M', 5),
    )),
}


# ================================== КЛАССЫ ===================================

# "Стражник" для значения по умолчанию ActionContextDeclaration.
_none = object()


class ActionContextDeclaration(object):
    u"""
    Класс, который определяет правило извлечения параметра из
    запроса и необходимость его наличия в объекте контекста ActionContext.

    :param str name: имя параметра

    :param type: тип извлекаемого значения

    :param bool required: указывает что параметр обязательный

    :param default: значение параметра по умолчанию, используется если его нет
        в запросе, но наличие обязательно

    :param unicode verbose_name: человеческое имя параметра,
        необходимо для сообщений об ошибках
    """
    def __init__(
            self, name='', default=_none, type=None,
            required=False, verbose_name='', *args, **kwargs):
        assert type, 'type must be defined!'
        self.name = name
        self.default = default
        self.required = required
        self.type = type
        self.verbose_name = verbose_name

    def human_name(self):
        """
        Возвращает человеческое название параметра *verbose_name*
        """
        return self.verbose_name if self.verbose_name else self.name


class ActionContext(object):
    u'''
    Контекст выполнения операции, восстанавливаемый из запроса.
    '''
    #: Для совместимости
    RequiredFailed = RequiredFailed

    class ValuesList():
        u"""
        Класс для описания параметров, которые будут передаваться в виде
        списка значений, разделенных определенным символом
        """
        def __init__(self, separator=',', type=int, allow_empty=True):
            # разделитель элементов
            self.separator = separator
            # тип, к которому будут преобразовываться эл-ты списка
            self.type = type
            self.allow_empty = allow_empty

    def __init__(self, **kwargs):
        u"""
        Параметры kwargs для быстрой инициализации
        """
        for k, v in kwargs.items():
            setattr(self, k, v)

    def convert_value(self, raw_value, arg_type):
        u'''
        Возвращает значение *raw_value*,
        преобразованное в заданный тип *arg_type*
        '''
        try:
            if isinstance(arg_type, ActionContext.ValuesList):
                elements = raw_value.split(arg_type.separator)
                if not arg_type.allow_empty:
                    elements = [elem for elem in elements if elem]
                value = [
                    self.convert_value(e, arg_type.type)
                    for e in elements
                ]
            else:
                if arg_type not in _PARSERS:
                    raise TypeError('Unknown parser "%r"!' % arg_type)
                value = _PARSERS[arg_type](raw_value)
        except AssertionError:
            raise ConversionFailed(value=raw_value, type=arg_type)

        return value

    def build(self, request, rules):
        u'''
        Выполняет заполнение собственных атрибутов
        согласно переданному запросу, исходя из списка правил

        :param request:запрос, на основе которого производится
            заполнение контекста
        :type reques: django.http.Request

        :param rules: правила извлечения контекста из запроса
        :type rules: список m3_core.actions.context.ActionContextDeclaration
        '''

        params = {}
        if rules:
            for rule in rules:
                # [тип параметра; признак того, что параметр включен в context]
                params[rule.name] = [rule.type, False]

        key = value = ptype = None
        try:
            # переносим параметры в контекст из запроса
            for key, value in iteritems(get_request_params(request)):
                # Пустые параметры не конвертируем,
                # т.к. они могут вызвать ошибку
                if not value:
                    continue

                if key in params:
                    ptype = params[key][0]
                    value = self.convert_value(value, ptype)
                    # Флаг того, что параметр успешно расшифрован
                    # и добавлен в контекст
                    params[key][1] = True
                setattr(self, key, value)
        except IOError as err:
            # В некоторых браузерах (предполагается что в ie)
            # происходит следующие:
            # REQUEST читается и в какой-то момент связь прекращается
            # из-за того, что браузер разрывает соединение,
            # в следствии этого происходит ошибка
            # IOError: request data read error
            logger.warning(str(err))
        except ValueError as err:
            # если ошибка преобразования,
            # то пусть проставится значение по-умолчанию
            logger.exception(
                u'Ошибка при преобразовании значения '
                u'из запроса %s="%s" к типу "%s"'
                % (key, value, ptype)
            )

        # переносим обязательные параметры, которые не встретились в запросе
        for rule in rules if rules else []:
            if rule.required and rule.default is not _none and (
                    not params[rule.name][1]):
                # если параметр не передан в запросе, но
                # он является обязательным и задано значение по умолчанию,
                # то помещаем этот параметр в контекст
                setattr(self, rule.name, rule.default)
        # проверяем наличие обязательных параметров
        self.check_required(rules)

    def check_required(self, rules):
        u"""
        Проверяет наличие обязательных параметров

        :param rules: правила извлечения контекста из запроса
        :type rules: список m3_core.actions.context.ActionContextDeclaration

        :raise: ActionContext.RequiredFailed
        """
        if not rules:
            return
        for rule in rules:
            if rule.required and getattr(self, rule.name, _none) is _none:
                raise ActionContext.RequiredFailed(rule.human_name())

    def json(self):
        u"""
        Рендеринг контекста в виде javascript объекта
        """
        def encoder_extender(obj):
            if isinstance(obj, datetime.datetime):
                result = _date2str(obj)
            # WTF? А где время в верхней строке?
            if isinstance(obj, datetime.date):
                result = _date2str(obj)
            elif isinstance(obj, datetime.time):
                result = _date2str(obj, '%H:%M')
            else:
                result = force_text(obj)

            return result

        # в json попадут только публичные атрибуты контекста,
        # и при этом только те, которые не callable
        data = dict(
            (k, v)
            for k, v in iteritems(self.__dict__)
            if not (
                k.startswith('_') or
                callable(v)
            )
        )
        return json.dumps(data, default=encoder_extender)

    def combine(self, context):
        u"""
        Объединение контекстов друг с другом.

        :param context: контекст, который объединяеться с текущим
        :type context: m3_core.actions.context.ActionContext

        :return: новый экземпляр контекста, который получился в результате
            слияния с текущим. Все существовавшие значения сохраняются.
        :rtype: m3_core.actions.context.ActionContext
        """
        result = ActionContext()
        if context:
            result.__dict__.update(context.__dict__)
        result.__dict__.update(self.__dict__)
        return result


# -----------------------------------------------------------------------------
class DeclarativeActionContext(ActionContext):
    """
    ActionContext, использующий декларативное описание контекста
    """
    # "встроенные" парсеры
    _parsers = {
        # булево значение
        'boolean': ('true', 'True', '1', 'on', 'yes').__contains__,

        # json в виде строки
        'json': json.loads,

        # дата/время
        'datetime': _datetime_parser,
        'date': _date_parser,
        'time': _time_parser,

        # простые типы
        'int': int,
        'float': float,
        'str': str,
        'unicode': text_type,
        'decimal': Decimal,
    }

    _mode = None

    def build(self, request, rules):
        """
        Выполняет заполнение собственных атрибутов
        согласно переданному запросу, исходя из списка правил

        :param request:запрос, на основе которого производится
            заполнение контекста
        :type reques: django.http.Request

        :param rules: правила извлечения контекста из запроса
        :type rules: список m3_core.actions.context.ActionContextDeclaration

        :raise: TypeError, ContextBuildingError, CriticalContextBuildingError
        """
        assert self.matches(rules), "rules must be a dict or pair!"

        # определяем режим, если правилла описаны парой
        if isinstance(rules, tuple):
            # режим
            mode = get_request_params(request).get(rules[0])
            try:
                # правила для конкретного режима
                rules = rules[1][mode]
            except KeyError:
                raise TypeError('Неизвестный режим: %r=%r' % (rules[0], mode))
            # ну и запоминаем режим
            self._mode = mode

        # аккумуляторы ошибок, связанных с нехваткой и неправильным форматом
        requiremets = []
        errors = []
        only_noncritical = True

        for key, parser_data in iteritems(rules):
            parser = parser_data['type']
            if not callable(parser):
                try:
                    parser = self._parsers[parser]
                except KeyError:
                    raise TypeError(
                        'Неизвестный парсер контекста: "%s"' %
                        parser
                    )

            add_error_to = None
            try:
                val = get_request_params(request).get(key)
                if val is None:
                    if 'default' in parser_data:
                        val = parser_data['default']
                    else:
                        # параметр обязателен, но не присутствует в запросе
                        add_error_to = requiremets
                else:
                    val = parser(val)
            except (ValueError, TypeError, KeyError, IndexError):
                # ошибка преобразования
                add_error_to = errors

            if add_error_to is not None:
                add_error_to.append(
                    parser_data.get('verbose_name', key))
                # ошибка критична, если хотя бы один из параметров
                # не имеет verbose_name
                only_noncritical = only_noncritical and (
                    'verbose_name' in parser_data)
                continue

            setattr(self, key, val)

        if requiremets or errors:
            if only_noncritical:
                raise ContextBuildingError(requiremets, errors)
            else:
                raise CriticalContextBuildingError(requiremets, errors)

    @classmethod
    def register_parser(cls, name, parser):
        """
        Регистрация парсера

        :param parser: парсер
        :type parser: callable-object

        :param unicode name: имя, подо которым регистрируется парсер
        """
        assert callable(parser), "@parser must be a callable object"
        cls._parsers[name] = parser

    @classmethod
    def matches(self, data):
        """
        Возвращает True, если объект data "похож" на правила для
        DeclarativeActionContext
        :param data: проверяемый объект
        :param type: object

        :return: True, если data "похож" на набор правил
        :rtype: boolean
        """
        return isinstance(data, dict) or (
            isinstance(data, tuple) and
            len(data) == 2 and
            isinstance(data[0], string_types)
        )
