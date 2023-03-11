# coding: utf-8
from __future__ import unicode_literals

from abc import ABCMeta
from abc import abstractmethod
from argparse import ArgumentParser
from inspect import isclass
import os
import sys

from django import VERSION
from django.conf import settings
from django.core import management
from django.db import transaction as _transaction
from django.db.models.base import Model
from django.db.models.fields import FieldDoesNotExist
from django.db.models.fields.related import RelatedField
from django.db.models.manager import Manager as _Manager
import six


_VERSION = VERSION[:2]
_14 = _VERSION == (1, 4)

#: Минимальная подерживаемая версия Django.
MIN_SUPPORTED_VERSION = (1, 4)

#: Максимальная поддерживаемая версия Django.
MAX_SUPPORTED_VERSION = (2, 2)

assert MIN_SUPPORTED_VERSION <= _VERSION <= MAX_SUPPORTED_VERSION, (
    'Unsupported Django version: {}.{}'.format(*_VERSION)
)
# -----------------------------------------------------------------------------


def get_installed_apps():
    """Возвращает имена пакетов с django-приложениями.

    .. note::

       Невозможность обхода в цикле списка ``INSTALLED_APPS`` обусловлена
       тем, что начиная с Django 1.7 приложения проекта могут быть указаны
       как путь до класса с конфигурацией приложения, например как
       ``project.app1.apps.AppConfig``.
    """
    if _VERSION < (1, 7):
        result = settings.INSTALLED_APPS

    else:
        from django.apps import apps

        result = (
            app_config.name
            for app_config in apps.get_app_configs()
        )

    return result
# -----------------------------------------------------------------------------
# Загрузка модели


def get_model(app_label, model_name):
    """Возвращает класс модели.

    :param str app_label: Имя приложения модели.
    :param str model_name: Имя модели (без учета регистра символов).

    :rtype: :class:`django.db.models.base.ModelBase`
    """
    if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 6):
        from django.db.models.loading import get_model as get_model_
        result = get_model_(app_label, model_name)
    else:
        from django.apps import apps
        result = apps.get_model(app_label, model_name)

    return result
# -----------------------------------------------------------------------------
# Модель "Учетная запись"


#: Содержит имя приложения и имя класса модели учетной записи.
#:
#: Строка содержит данные в виде, пригодном для использования при описании
#: внешних ключей (``ForeignKey``, ``OneToOneField`` и т.п.), ссылающихся
#: на модель учетной записи.
#:
#: Если подключено приложение ``'django.contrib.auth'``, то для Django 1.4
#: всегда содержит значение ``'auth.User'``, а для более старших версий --
#: значение параметра ``AUTH_USER_MODEL`` из настроек системы. Если же
#: приложение ``'django.contrib.auth'`` не подключено, содержит ``None``.
#:
#: .. code::
#:
#:    from m3_django_compat import AUTH_USER_MODEL
#:
#:    class Person(models.Model):
#:        user = models.ForeignKey(AUTH_USER_MODEL)
AUTH_USER_MODEL = None
if any(
    app_package_name.startswith('django.contrib.auth')
    for app_package_name in settings.INSTALLED_APPS
):
    AUTH_USER_MODEL = 'auth.User' if _14 else settings.AUTH_USER_MODEL


def get_user_model():
    """Возвращает класс модели учетной записи.

    Если подключено приложение ``'django.contrib.auth'``, то для Django 1.4
    возвращает :class:`django.contrib.auth.models.User`, а для
    версий 1.5 и старше - результат вызова
    :func:`django.contrib.auth.get_user_model`.

    :rtype: :class:`django.db.models.base.ModelBase` or :class:`NoneType`
    """
    if 'django.contrib.auth' not in get_installed_apps():
        result = None
    elif _14:
        result = get_model('auth', 'User')
    else:
        from django.contrib.auth import get_user_model as _get_user_model
        result = _get_user_model()

    return result
# -----------------------------------------------------------------------------
# Транзакции


def in_atomic_block(using=None):
    """Возвращает ``True``, если в момент вызова открыта транзакция.

    Если включен режим автоподтверждения (autocommit), то возвращает ``False``.

    :param str using: Алиас базы данных.

    :rtype: bool
    """
    if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 5):
        result = _transaction.is_managed(using)
    else:
        result = _transaction.get_connection(using).in_atomic_block

    return result


if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 5):
    class _Atomic(object):

        def __init__(self, savepoint):
            self._savepoint = savepoint
            self._sid = None

        def entering(self, using):
            # pylint: disable=attribute-defined-outside-init
            if in_atomic_block(using):
                if self._savepoint:
                    self._sid = _transaction.savepoint(using)
            else:
                self._commit_on_exit = True

                _transaction.enter_transaction_management(using=using)
                _transaction.managed(True, using=using)

        def exiting(self, exc_value, using):
            if self._sid:
                if self._savepoint:
                    if exc_value is None:
                        _transaction.savepoint_commit(self._sid, using)
                    else:
                        _transaction.savepoint_rollback(self._sid, using)
            else:
                try:
                    if exc_value is not None:
                        if _transaction.is_dirty(using=using):
                            _transaction.rollback(using=using)
                    else:
                        if _transaction.is_dirty(using=using):
                            try:
                                _transaction.commit(using=using)
                            except:  # noqa
                                _transaction.rollback(using=using)
                                raise
                finally:
                    _transaction.leave_transaction_management(using=using)


def atomic(using=None, savepoint=True):
    """Совместимый аналог декоратора/менеджера контекста ``atomic``.

    В Django>=1.6 задействует функционал ``atomic``, а в версиях ниже 1.6
    имитирует его поведение средствами модуля ``django.db.transaction``, при
    этом, в отличие от ``commit_on_success`` из Django<1.6, поддерживает
    вложенность.

    :param str using: Алиас базы данных. Если указано значение ``None``, будет
        использован алиас базы данных по умолчанию.
    :param bool savepoint: Определяет, будут ли использоваться точки сохранения
        (savepoints) при использовании вложенных ``atomic``.
    """
    if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 5):
        if callable(using):
            # atomic вызван как декоратор без параметров
            from django.db.utils import DEFAULT_DB_ALIAS
            func = using
            using = DEFAULT_DB_ALIAS
        else:
            func = None

        atomic_ = _Atomic(savepoint)
        result = _transaction._transaction_func(
            atomic_.entering, atomic_.exiting, using
        )

        if func:
            result = result(func)
    else:
        result = _transaction.atomic(using, savepoint)

    return result


def commit_unless_managed(using=None):
    """Совместимый аналог функции commit_unless_managed.

    В Django 1.6+ эта функция была помечена, как устаревшая, а в Django 1.8+
    была удалена.
    """
    if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 5):
        from django.db.transaction import commit_unless_managed as func
        return func(using)
# -----------------------------------------------------------------------------
# Обеспечение совместимости менеджеров моделей


class Manager(_Manager):

    """Базовый класс для менеджеров моделей.

    Создан в связи с переименованием в Django 1.6 метода ``get_query_set`` в
    ``get_queryset`` и ``get_prefetch_query_set`` в ``get_prefetch_queryset``.

    "Пробрасывает" вызовы этих методов к методам
    :class:`django.db.models.manager.Manager`, соответствующим используемой
    версии Django.

    Предназначен для использования в качестве базового класса вместо
    :class:`django.db.models.manager.Manager`.
    """

    if (1, 6) <= _VERSION <= (1, 7):
        # Подавление предупреждения о необходимости переименования методов.
        from django.db.models.manager import RenameManagerMethods

        # pylint: disable=invalid-name
        class __metaclass__(RenameManagerMethods):
            renamed_methods = ()

    def __get_queryset_method(self):
        if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 5):
            result = super(Manager, self).get_query_set
        else:
            result = super(Manager, self).get_queryset

        return result

    @property
    def get_queryset(self):
        return self.__get_queryset_method()

    @property
    def get_query_set(self):
        return self.__get_queryset_method()

    def __get_prefetch_queryset_method(self):
        if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 5):
            result = super(Manager, self).get_prefetch_query_set
        else:
            result = super(Manager, self).get_prefetch_queryset

        return result

    @property
    def get_prefetch_queryset(self):
        return self.__get_prefetch_queryset_method()

    @property
    def get_prefetch_query_set(self):
        return self.__get_prefetch_queryset_method()
# -----------------------------------------------------------------------------
# Базовый класс для загрузчика шаблонов


if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7):
    from django.template.loader import BaseLoader
else:
    from django.template.loaders.base import Loader as BaseLoader
# -----------------------------------------------------------------------------
# Средства обеспечения совместимости с разными версиями Model API


class RelatedObject(object):

    """Совместимый аналог RelatedObject."""

    def __init__(self, relation):
        self.relation = relation

    def __repr__(self, *args, **kwargs):
        return '{}: {}'.format(self.__class__.__name__, self.relation)

    def __getattr__(self, name):
        return getattr(self.relation, name)

    @property
    def model_name(self):
        if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7):
            result = self.relation.var_name
        else:
            result = self.relation.related_model._meta.model_name
        return result

    @property
    def parent_model(self):
        return self.relation.model


def get_related(field):
    """Возвращает RelatedObject для поля модели.

    :param field: Поле модели.
    :type field: django.db.models.fields.related.RelatedField
    """
    assert isinstance(field, RelatedField), field

    if _VERSION <= (1, 7):
        result = field.related
    elif _VERSION == (1, 8):
        result = RelatedObject(field.related)
    else:
        result = RelatedObject(field.remote_field)
    return result


class ModelOptions(object):

    """Совместимые параметры модели (``Model._meta``).

    Предоставляет набор методов, которые были доступны в Django<=1.7, а в
    Django>=1.8 были помечены, как устаревшие и будут удалены в Django 2.0.

    .. seealso::

       `Migrating from the old API <https://goo.gl/mzdNSH>`_.
    """

    def __init__(self, model):
        self.model = model if isclass(model) else model.__class__
        self.opts = getattr(model, '_meta', None)
        self.is_django_model = (
            self.opts is None or
            issubclass(self.model, Model)
        )

    def get_field(self, name):
        if (not self.is_django_model or
                MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7)):
            return self.opts.get_field(name)
        else:
            field = self.opts.get_field(name)

            if (field.auto_created or
                    field.is_relation and field.related_model is None):
                raise FieldDoesNotExist("{} has no field named '{}'"
                                        .format(self.model.__name__, name))

            return field

    def get_field_by_name(self, name):
        if (not self.is_django_model or
                MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7)):
            result = self.opts.get_field_by_name(name)
        else:
            field = self.opts.get_field(name)

            result = (
                field,
                field.model,
                not field.auto_created or field.concrete,
                field.many_to_many,
            )
        return result

    def get_all_related_objects(self):
        if (not self.is_django_model or
                MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7)):
            result = [
                RelatedObject(relation)
                for relation in self.model._meta.get_all_related_objects()
            ]
        else:
            result = [
                RelatedObject(field)
                for field in self.model._meta.get_fields()
                if (
                    (field.one_to_many or field.one_to_one) and
                    field.auto_created
                )
            ]
        return result

    def get_m2m_with_model(self):
        if (not self.is_django_model or
                MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7)):
            result = self.opts.get_m2m_with_model()
        else:
            result = [
                (
                    field,
                    field.model if field.model != self.model else None
                )
                for field in self.opts.get_fields()
                if field.many_to_many and not field.auto_created
            ]
        return result
# -----------------------------------------------------------------------------
# Доступ к HttpRequest.REQUEST


def get_request_params(request):
    """Возвращает параметры HTTP-запроса вне зависимости от его типа.

    В Django<=1.8 параметры были доступны в атрибуте ``REQUEST``, но в
    Django>=1.9 этот атрибут был удален (в 1.7 - помечен, как устаревший).
    """
    if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 7):
        result = request.REQUEST
    else:
        if request.method == 'GET':
            result = request.GET
        elif request.method == 'POST':
            result = request.POST
        else:
            result = {}

    return result
# -----------------------------------------------------------------------------


class TemplateWrapper(object):

    """Класс-обертка для шаблонов Django.

    Обеспечивает возможность передачи в метод ``render`` как экземпляров
    :class:`django.template.Context` или
    :class:`django.template.RequestContext`, либо словарей.
    """

    def __init__(self, template):
        self._template = template

    def __getattr__(self, name):
        return getattr(self._template, name)

    def render(self, context=None, request=None):
        from django.template.context import Context as C
        from django.template.context import RequestContext as RC
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

        if isinstance(context, C) and _VERSION <= (1, 6):
            # Backport метода Context.flatten из Django 1.7
            def flatten(self):
                flat = {}
                for d in self.dicts:
                    flat.update(d)
                return flat

            from types import MethodType

            context.flatten = MethodType(flatten, context, type(context))
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

        if isinstance(context, RC):
            if _VERSION <= (1, 7):
                result = self._template.render(context)
            else:
                result = self._template.render(
                    context.flatten(), context.request
                )

        elif isinstance(context, C):
            if _VERSION <= (1, 7):
                if request:
                    result = self._template.render(
                        RC(request, context.flatten())
                    )
                else:
                    result = self._template.render(context)
            else:
                result = self._template.render(context.flatten(), request)

        else:
            if _VERSION <= (1, 7):
                if request:
                    result = self._template.render(RC(request, context))
                else:
                    result = self._template.render(C(context))
            else:
                result = self._template.render(context, request)

        return result


def get_template(*args, **kwargs):
    """Совместимый аналог функции :func:`django.template.loader.get_template`.

    Позволяет вызывать метод ``render()`` шаблона передавая в качестве
    аргумента экземпляры :class:`django.template.Context`,
    :class:`django.template.RequestContext` или :class:`dict` вне зависимости
    от версии Django.

    :rtype: django.template.Template
    """
    from django.template.loader import get_template as _get_template
    return TemplateWrapper(_get_template(*args, **kwargs))
# -----------------------------------------------------------------------------


@six.add_metaclass(ABCMeta)
class DatabaseRouterBase(object):

    """Базовый класс для роутеров баз данных.

    Обеспечивает совместимость роутера для разных версий Django в части методов
    :meth:`allow_sync` и :meth:`allow_migrate`.

    В потомках нужно реализовать метод :meth:`_allow`.
    """

    @abstractmethod
    def _allow(self, db, app_label, model_name):
        """Возвращает True, если разрешена синхронизация/миграция для модели.

        :param str db: Алиас базы данных.
        :param str app_label: Название приложения.
        :param str model_name: Имя модели.

        :rtype: bool
        """

    if _VERSION <= (1, 6):
        def allow_syncdb(self, db, model):
            app_label = model._meta.app_label
            model_name = model.__name__
            return self._allow(db, app_label, model_name)
    elif _VERSION == (1, 7):
        def allow_migrate(self, db, model):
            app_label = model._meta.app_label
            model_name = model.__name__
            return self._allow(db, app_label, model_name)
    else:
        def allow_migrate(self, db, app_label, model_name=None, **hints):
            return self._allow(db, app_label, model_name)
# -----------------------------------------------------------------------------


class CommandParser(ArgumentParser):

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        super(CommandParser, self).__init__(**kwargs)

    def parse_args(self, args=None, namespace=None):
        # Catch missing argument for a better error message
        if (hasattr(self.cmd, 'missing_args_message') and
                not (args or any(
                    not arg.startswith('-') for arg in args))):
            self.error(self.cmd.missing_args_message)
        return super(CommandParser, self).parse_args(args,
                                                     namespace)

    def error(self, message):
        if self.cmd._called_from_command_line:
            super(CommandParser, self).error(message)
        else:
            raise management.CommandError("Error: %s" % message)


class BaseCommand(management.BaseCommand):  # pylint: disable=abstract-method

    """Базовый класс для management-команд, использующий argparse."""

    def add_arguments(self, parser):
        pass

    def create_parser(self, prog_name, subcommand):
        parser = CommandParser(
            self, prog="%s %s" % (os.path.basename(prog_name), subcommand),
            description=self.help or None,
        )
        parser.add_argument('--version', action='version',
                            version=self.get_version())
        parser.add_argument(
            '-v', '--verbosity', action='store', dest='verbosity',
            default=1,
            type=int, choices=[0, 1, 2, 3],
            help='Verbosity level; 0=minimal output, 1=normal output, '
                 '2=verbose output, 3=very verbose output',
        )
        parser.add_argument(
            '--settings',
            help=(
                'The Python path to a settings module, e.g. '
                '"myproject.settings.main". If this isn\'t provided, the '
                'DJANGO_SETTINGS_MODULE environment variable will be used.'
            ),
        )
        parser.add_argument(
            '--pythonpath',
            help='A directory to add to the Python path, e.g. '
                 '"/home/djangoprojects/myproject".',
        )
        parser.add_argument('--traceback', action='store_true',
                            help='Raise on CommandError exceptions')

        if _VERSION >= (1, 7):
            parser.add_argument(
                '--no-color', action='store_true', dest='no_color',
                default=False,
                help="Don't colorize the command output.",
            )

        if _VERSION >= (2,2):
            parser.add_argument(
                '--force-color', action='store_true',
                help='Force colorization of the command output.',
            )

        parser.add_argument('args', nargs='*')

        self.add_arguments(parser)

        return parser

    if _VERSION < (1, 8):
        def run_from_argv(self, argv):
            from django.core.management import handle_default_options
            from django.core.management import CommandError

            self._called_from_command_line = True
            parser = self.create_parser(argv[0], argv[1])

            options = parser.parse_args(argv[2:])
            cmd_options = vars(options)
            # Move positional args out of options to mimic legacy optparse
            args = cmd_options.pop('args', ())

            handle_default_options(options)
            try:
                self.execute(*args, **cmd_options)
            except Exception as e:  # pylint: disable=broad-except
                if options.traceback or not isinstance(e, CommandError):
                    raise

                # SystemCheckError takes care of its own formatting.
                if isinstance(e, CommandError):
                    self.stderr.write(str(e), lambda x: x)
                else:
                    self.stderr.write('%s: %s' % (e.__class__.__name__, e))
                sys.exit(1)
# -----------------------------------------------------------------------------
# Типы для wrapper-ов функций встроенных типов


if sys.version_info < (3, 7):
    WrapperDescriptorType = type(object.__init__)
    MethodWrapperType = type(object().__init__)
    MethodDescriptorType = type(str.join)
else:
    from types import WrapperDescriptorType
    from types import MethodWrapperType
    from types import MethodDescriptorType
# -----------------------------------------------------------------------------
# Функции для совместимости c django 2.0


def is_authenticated(user):
    """Возвращает True, если пользователь аутентифицирован.

    :param user: Объект модели пользователя из settings.AUTH_USER_MODEL.
    :type user: django.contrib.auth.base_user.AbstractBaseUser

    :rtype: bool

    """
    if MIN_SUPPORTED_VERSION <= _VERSION <= (1, 11):
        return user.is_authenticated()
    else:
        return user.is_authenticated

# -----------------------------------------------------------------------------
