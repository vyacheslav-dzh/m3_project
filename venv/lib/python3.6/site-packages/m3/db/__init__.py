# coding:utf-8
from __future__ import absolute_import

import datetime

from django.db import connections
from django.db import models
from django.db import router
from django.db.models.deletion import Collector
from django.db.models.query import QuerySet
from m3 import RelatedError
from m3 import json_encode
from m3_django_compat import Manager
from m3_django_compat import commit_unless_managed
import six


def safe_delete(model):
    """
    Функция выполняющая "безопасное" удаление записи из БД.

    В случае, если удаление не удалось по причине нарушения целостности,
    то возвращается false. Иначе, true
    к тому же функция пересчитывает MPTT индексы дерева
    т.к. стандартный пересчет запускается при вызове model_instance.delete()
    """
    models.signals.pre_delete.send(sender=model.__class__, instance=model)
    db_alias = router.db_for_write(model.__class__, instance=model)
    try:
        connection = connections[db_alias]
        cursor = connection.cursor()
        sql = "DELETE FROM %s WHERE id = %s" % (
            connection.ops.quote_name(model._meta.db_table), model.id)
        cursor.execute(sql)
        commit_unless_managed(using=db_alias)
    except Exception as e:
        # Встроенный в Django IntegrityError не генерируется.
        # Кидаются исключения, специфичные для каждого драйвера БД.
        # Но по спецификации PEP 249 все они называются IntegrityError
        if e.__class__.__name__ == 'IntegrityError':
            return False
        raise

    # добавим пересчет mptt дерева
    # (т.к. стандартный пересчет вешается на метод self.delete()
    if hasattr(model, '_tree_manager') and callable(
            getattr(model._tree_manager, '_close_gap', None)):
        # это, видимо, mptt модель
        opts = model._meta
        right = getattr(model, getattr(opts, 'right_attr', 'rght'))
        left = getattr(model, getattr(opts, 'left_attr', 'lft'))
        tree = getattr(model, getattr(opts, 'tree_id_attr', 'tree_id'))
        model._tree_manager._close_gap(right - left + 1, right, tree)

    models.signals.post_delete.send(sender=model.__class__, instance=model)
    return True


def queryset_limiter(queryset, start=0, limit=0):
    """
    "Вырезает" из QuerySet'a записи начиная с позиции start
    до записи start+limit.
    Возвращает (rows, total, ), где
    rows -  QuerySet с вырезанными записями
    total - общее кол-во записей в queryset'e
    """

    assert (isinstance(queryset, QuerySet) or getattr(queryset, '__iter__')), (
        'queryset must be either instance of '
        'django.db.models.query.QuerySet or iterable'
    )

    if start < 0:
        start = 0
    if limit < 0:
        limit = 0
    total = (
        queryset.count()
        if isinstance(queryset, QuerySet)
        else len(queryset)
    )
    rows = queryset[start:start+limit]
    return rows, total


class BaseEnumerate(object):
    """
    Базовый класс для создания перечислений.
    """
    # В словаре values описываются перечисляемые константы
    # и их человеческое название
    # Например: {STATE1: u'Состояние 1', CLOSED: u'Закрыто'}
    values = {}

    @classmethod
    def get_choices(cls):
        """
        Используется для ограничения полей ORM и в качестве источника данных
        в ArrayStore и DataStore ExtJS
        """
        return list(cls.values.items())

    get_items = get_choices

    @classmethod
    def get_constant_value_by_name(cls, name):
        """
        Возвращает значение атрибута константы, которая используется в
        качестве ключа к словарю values
        """
        if not isinstance(name, six.string_types):
            raise TypeError("'name' must be a string")

        if not name:
            raise ValueError("'name' must not be empty")

        return cls.__dict__[name]


@six.python_2_unicode_compatible
class BaseObjectModel(models.Model):
    """
    Базовая модель для объектов системы.
    Сюда будут добавляться общие свойства и методы,
    которые могут быть перекрыты в дальнейшем
    """
    @json_encode
    def display(self):
        """
        Отображение объекта по-умолчанию. Отличается от __unicode__ тем,
        что вызывается при json сериализации в m3.core.json.M3JSONEncoder
        """
        return six.text_type(self)

    def __str__(self):
        """ Определяет текстовое представление объекта """
        name = getattr(self, 'name', None) or getattr(self, 'fullname', None)
        if name:
            if callable(name):
                name = name()
            return u'{%s: %s}' % (self.pk, name)
        else:
            return u'{%s}' % self.pk

    @classmethod
    def get_verbose_name(cls):
        return cls._meta.verbose_name

    def safe_delete(self):
        """
        Функция выполняющая "безопасное" удаление записи из БД.
        В случае, если запись не удалось удалить по причине нарушения
        целостности, возвращается False, иначе True.
        """
        if not safe_delete(self):
            raise RelatedError(
                u"Объект не может быть удален! Возможно на него есть ссылки.")
        else:
            return True

    def get_related_objects(self, using=None):
        """
        Возвращает структуру содержащую классы моделей,
        первичные ключи и экземпляры записей, зависящие от текущей записи.
        Возвращаемая структура имеет вид:
        [(КлассМодели1,
            {id1: ЭкземплярМодели1cID1, id2: ЭкземплярМодели1cID2, ...}),
         (КлассМодели2,
            {id1: ЭкземплярМодели2cID1, id2: ЭкземплярМодели2cID2, ...} },
        ...]
        @deprecated: Вытаскивает много данных. Сервер может зависнуть!
        """
        using = using or router.db_for_write(self.__class__, instance=self)
        collector = Collector(using=using)
        collector.collect([self])
        return list(collector.data.items())

    def delete_related(self, affected=None, using=None):
        """
        Стандартное каскадное удаление объектов в django,
        дополненное проверкой на удаляемые классы моделей affected.
        По умолчанию affected содержит пустой список
        - это ограничивает удаляемые модели только текущим классом.
        К перечисленным в affected классам текущий добавляется автоматически.
        Если удаление не удалось выполнить, возвращает False, иначе True.
        Пример:
            Model1.objects.get(id=1).delete_related(affected=[Model2, Model3])
        """
        # Кописаст из django.db.models.Model delete()
        using = using or router.db_for_write(self.__class__, instance=self)
        assert self._get_pk_val() is not None, (
            "%s object can't be deleted because its "
            "%s attribute is set to None."
        ) % (
            self._meta.object_name,
            self._meta.pk.attname
        )

        collector = Collector(using=using)
        collector.collect([self])
        # cut

        affected = affected or []
        assert isinstance(affected, list), (
            'Affected models must be the list type')
        affected.append(self.__class__)

        for model in collector.data.keys():
            if model not in affected:
                return False

        collector.delete()
        return True

    class Meta:
        abstract = True


##############################################################
# По мотивам https://coderanger.net/2011/01/select-for-update/
class ForUpdateQuerySet(QuerySet):
    def for_update(self):
        if 'sqlite' in connections[self.db].settings_dict['ENGINE'].lower():
            # Noop on SQLite since it doesn't support FOR UPDATE
            return self
        sql, params = self.query.get_compiler(self.db).as_sql()
        return self.model._default_manager.raw(
            sql.rstrip() + ' FOR UPDATE', params)


class ForUpdateManager(Manager):
    def get_queryset(self):
        return ForUpdateQuerySet(self.model, using=self._db)
##############################################################


class ConcurrentEditError(Exception):
    """
    Исключение возникающее при попытке сохранения записи,
    которая была изменена после ее чтения.
    """
    pass


class BaseObjectModelWVersion(BaseObjectModel):
    """
    Базовый класс для версионных записей.
    Нужен для реализации оптимистичной обработки блокировки
    """

    objects = ForUpdateManager()

    version = models.IntegerField(u'Версия записи', default=0)

    def do_lock(self):
        if self.id:
            # блокируем запись с нашей версией от изменения
            q = self.__class__.objects.filter(
                id=self.id, version=self.version
            ).for_update()
            # если удачно блокировали,
            # то можем делать с ней что угодно в рамках транзакции
            if len(list(q)) == 1:
                return True
            else:
                # если блокировать нечего, то значит кто-то ее поменял
                return False
        else:
            return True

    def save(self, *a, **k):
        """
        При каждом сохранении номер версии увеличивается.
        Это нужно чтобы проверять, была ли модифицирована запись
        после последнего получения
        """
        if self.id:
            self.version += 1
        super(BaseObjectModelWVersion, self).save(*a, **k)

    class Meta:
        abstract = True


class ObjectState(BaseEnumerate):
    """
    Состояние объекта
    Используется для определения логики использования записи:
    - если запись "Действует", значит нет ограничений на ее использование
    - если запись "Закрыта", значит ее нельзя использовать в новых документах,
      но можно выводить в отчетах и уже существующих данных
    - если запись "Черновик", значит ее нельзя использовать в логике приложения
      и в отчетах. По сути, это означает,
      что запись введена не полностью и не утверждена
    """
    VALID = 0
    CLOSED = 1
    DRAFT = 2
    values = {VALID: u'Действует', CLOSED: u'Закрыта', DRAFT: u'Черновик'}


class ObjectManager(Manager):
    """
    Менеджер запросов к записям справочника
    Фильтрует записи по периоду действия и состоянию
    """
    def get_default_state(self):
        """
        Для прикладного переопределения состояний, выбираемых по-умолчанию
        """
        return [ObjectState.VALID]

    def __init__(self, date=None, state=None, *a, **kw):
        super(ObjectManager, self).__init__(*a, **kw)
        self.query_on_date = date
        if state:
            if isinstance(state, type([])):
                self.query_state = state
            else:
                self.query_state = [state]
        else:
            self.query_state = self.get_default_state()

    def get_queryset(self):
        # если указывали дату,
        # то отфильтруем на дату, иначе только по состоянию
        if self.query_on_date:
            return super(ObjectManager, self).get_queryset().filter(
                begin__lte=self.query_on_date,
                end__gt=self.query_on_date,
                state__in=self.query_state
            )
        else:
            return super(ObjectManager, self).get_queryset().filter(
                state__in=self.query_state
            )


class BaseObjectModelWState(BaseObjectModel):
    """
    Базовый класс для всех моделей состоянием и периодом действия
    """

    state = models.SmallIntegerField(
        u'Состояние',
        choices=ObjectState.get_choices(),
        default=ObjectState.DRAFT
    )
    begin = models.DateTimeField(
        u'Начало действия',
        null=True,
        blank=True,
        db_index=True,
        default=datetime.date.min
    )
    end = models.DateTimeField(
        u'Окончание действия',
        null=True,
        blank=True,
        db_index=True,
        default=datetime.date.max
    )

    @classmethod
    def get_objects_on_date(cls, date=None):
        """
        Получает менеджер с параметрами.
        Можно писать так: Model.objects_on_date(datetime.today).filter....
        """
        manager = ObjectManager(date=date)
        manager.model = cls
        return manager

    objects_on_date = get_objects_on_date

    class Meta:
        abstract = True
