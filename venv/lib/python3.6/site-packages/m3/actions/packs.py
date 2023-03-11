# coding: utf-8
u"""Паки и экшены для работы со справочниками."""
from __future__ import absolute_import

from logging import getLogger

from m3 import RelatedError
from m3.actions import ACD
from m3.actions import Action
from m3.actions import ActionPack
from m3.actions import OperationResult
from m3.actions import PreJsonResult
from m3.actions import utils
from m3.actions.interfaces import ISelectablePack
from m3.actions.results import ActionResult
from m3.db import BaseObjectModel
from m3.db import safe_delete
from m3_django_compat import atomic
from m3_django_compat import get_installed_apps
from m3_django_compat import get_request_params
from m3_ext.ui.containers import ExtPagingBar
from m3_ext.ui.misc.store import ExtJsonStore
from m3_ext.ui.results import ExtUIScriptResult
from m3_ext.ui.windows.complex import ExtDictionaryWindow


logger = getLogger('django')


try:
    from m3_audit.manager import AuditManager
except ImportError:
    # При сборке документации, внешняя Django ничего не знает про m3_audit
    logger.warning('m3_audit import error')


MSG_DOESNOTEXISTS = (
    u'Запись справочника с id=%s не найдена в базе данных.<br/>'
    u'Возможно, она была удалена. Пожалуйста, обновите таблицу.'
)


class ObjectNotFound(Exception):
    """
    Исключение для виртуальных справочников. Аналог DoesNotExists у моделей.
    """
    pass


class DictListWindowAction(Action):
    """
    Действие, которое возвращает окно со списком элементов справочника.
    """
    url = '/list-window$'

    def create_window(self, request, context, mode):
        """
        Создаем и настраиваем окно
        """
        base = self.parent
        allow_copy = hasattr(base, 'allow_copy') and base.allow_copy
        win = base.list_form(mode=mode, title=base.title)
        win.allow_copy = allow_copy
        win.height, win.width = base.height, base.width
        win.min_height, win.min_width = base.height, base.width

        win.init_grid_components()
        if base.list_paging:
            win.grid.bottom_bar = ExtPagingBar(page_size=25)
        return win

    def create_columns(self, control, columns):
        """
        Добавляем отображаемые колонки. См. описание в базовом классе!
        """
        for column in columns:
            if isinstance(column, tuple):
                column_params = {
                    'data_index': column[0],
                    'header': column[1],
                    'sortable': True}

                if len(column) > 2:
                    column_params['width'] = column[2]
            elif isinstance(column, dict):
                column_params = column
            else:
                raise Exception('Incorrect parameter column.')
            control.add_column(**column_params)

    def configure_list(self, win):
        base = self.parent

        # Устанавливаем источники данных
        grid_store = ExtJsonStore(
            url=base.rows_action.get_absolute_url(),
            auto_load=True,
            remote_sort=True)
        grid_store.total_property = 'total'
        grid_store.root = 'rows'
        win.grid.set_store(grid_store)

        if not base.list_readonly:
            # Доступны 3 события: создание нового элемента,
            # редактирование или удаление имеющегося
            win.url_new_grid = base.edit_window_action.get_absolute_url()
            win.url_edit_grid = base.edit_window_action.get_absolute_url()
            win.url_delete_grid = base.delete_action.get_absolute_url()

            # Если разрешено копирование, то доступно ещё одно событие.
            if getattr(base, 'allow_copy', False):
                win.url_copy_grid = base.copy_action.get_absolute_url()

    def run(self, request, context):
        win = self.create_window(request, context, mode=0)
        self.create_columns(win.grid, self.parent.list_columns)
        self.configure_list(win)

        # проверим право редактирования
        if not self.parent.has_sub_permission(
                request.user, self.parent.PERM_EDIT, request):
            win.make_read_only()

        return ExtUIScriptResult(self.parent.get_list_window(win))


class DictSelectWindowAction(DictListWindowAction):
    """
    Действие, возвращающее окно с формой выбора из справочника
    """
    url = '/select-window$'

    def run(self, request, context):
        base = self.parent
        win = self.create_window(request, context, mode=1)
        win.modal = True
        self.create_columns(win.grid, self.parent.list_columns)
        self.configure_list(win)

        # M prefer 12.12.10 >
        # win.column_name_on_select = "name"
        # -----:
        win.column_name_on_select = base.column_name_on_select
        # prefer <

        # проверим право редактирования
        if not self.parent.has_sub_permission(
                request.user, self.parent.PERM_EDIT, request):
            win.make_read_only()

        return ExtUIScriptResult(self.parent.get_select_window(win))


class DictEditWindowAction(Action):
    """
    Редактирование элемента справочника
    """
    url = '/edit-window$'

    def context_declaration(self):
        return [ACD(name='id',
                    default=0,
                    type=int,
                    required=True,
                    verbose_name=u'id элемента справочника'),
                ACD(name='isGetData',
                    default=False,
                    type=bool,
                    required=True,
                    verbose_name=u'признак загрузки данных')]

    def run(self, request, context):
        base = self.parent
        is_get_data = context.isGetData
        # Получаем объект по id
        try:
            obj = base.get_row(context.id)
        except base._nofound_exception:
            return OperationResult.by_message(MSG_DOESNOTEXISTS % context.id)

        # Разница между новым и созданным объектов в том,
        # что у нового нет id или он пустой
        create_new = True
        if isinstance(obj, dict) and obj.get('id') is not None:
            create_new = False
        elif hasattr(obj, 'id') and getattr(obj, 'id') is not None:
            create_new = False
        if create_new and base.add_window:
            win = utils.bind_object_from_request_to_form(
                request, base.get_row, base.add_window)
        else:
            win = utils.bind_object_from_request_to_form(
                request, base.get_row, base.edit_window)

        if not win.title:
            win.title = base.title
        win.form.url = base.save_action.get_absolute_url()
        # укажем адрес для чтения данных
        win.data_url = base.edit_window_action.get_absolute_url()

        # проверим право редактирования
        if not self.parent.has_sub_permission(
                request.user, self.parent.PERM_EDIT, request):
            exclude_list = ['close_btn', 'cancel_btn']
            win.make_read_only(True, exclude_list)

        # У окна может быть процедура доп. конфигурации
        # под конкретный справочник
        if (hasattr(win, 'configure_for_dictpack') and
                callable(win.configure_for_dictpack)):
            win.configure_for_dictpack(action=self, pack=self.parent,
                                       request=request, context=context)

        if not is_get_data:
            # если запрашивали не данные - вернем окно
            return ExtUIScriptResult(base.get_edit_window(win))
        else:
            # если просили данные, то выжмем их из окна обратно в объект,
            # т.к. в окне могли быть и другие данных (не из этого объекта)
            data_object = {}
            # т.к. мы не знаем какие поля должны быть у объекта - создадим
            # все, которые есть на форме
            all_fields = win.form._get_all_fields(win)
            for field in all_fields:
                data_object[field.name] = None
            win.form.to_object(data_object)
            return PreJsonResult({'success': True, 'data': data_object})


class DictRowsAction(Action):
    """
    Действие, которое возвращает список записей справочника.
    Именно список записей, которые потом могут быть отображены в гриде.
    В качестве контекста выполнения может быть задано:
    a) текстовая строка с фильтром (для выполнения поиска);
    b) начальная позиция и смещение записей для пейджинга.
    """
    url = '/rows$'

    def run(self, request, context):
        offset = utils.extract_int(request, 'start')
        limit = utils.extract_int(request, 'limit')
        request_params = get_request_params(request)
        filter = request_params.get('filter')
        direction = request_params.get('dir')
        user_sort = request_params.get('sort')
        if direction == 'DESC':
            user_sort = '-' + user_sort
        dict_list = []
        for item in self.parent.list_columns:
            if isinstance(item, (list, tuple)):
                dict_list.append(item[0])
            elif isinstance(item, dict) and item.get('data_index'):
                dict_list.append(item['data_index'])

        if (hasattr(self.parent, 'modify_rows_query') and
                callable(self.parent.modify_rows_query)):
            rows = self.parent.get_rows_modified(
                offset, limit, filter, user_sort, request, context)
        else:
            rows = self.parent.get_rows(offset, limit, filter, user_sort)
        return PreJsonResult(
            rows, self.parent.secret_json, dict_list=dict_list)


class DictLastUsedAction(Action):
    """
    Действие, которое возвращает список последних использованных действий
    """
    url = '/last-rows$'

    def run(self, request, context):
        return PreJsonResult(self.parent.get_last_used(self))


class ListGetRowAction(Action):
    """
    Действие, которое отвечает за возврат данных для
    одного отдельно-взятой записи справочника
    """
    url = '/item$'

    def context_declaration(self):
        return [ACD(
            name='id',
            default=0,
            type=int,
            required=True,
            verbose_name=u'id элемента справочника')]

    def run(self, request, context):
        try:
            result = self.parent.get_row(context.id)
        except self.parent._nofound_exception:
            return OperationResult.by_message(MSG_DOESNOTEXISTS % context.id)
        return PreJsonResult(result)


class DictSaveAction(Action):
    """
    Действие выполняет сохранение записи справочника.
    """
    url = '/save$'

    def context_declaration(self):
        return [
            ACD(
                name='id',
                default=0,
                type=int,
                required=True,
                verbose_name=u'id элемента справочника')]

    def run(self, request, context):
        try:
            if not context.id and self.parent.add_window:
                obj = utils.bind_request_form_to_object(
                    request, self.parent.get_row, self.parent.add_window)
            else:
                obj = utils.bind_request_form_to_object(
                    request, self.parent.get_row, self.parent.edit_window)
        except self.parent._nofound_exception:
            return OperationResult.by_message(MSG_DOESNOTEXISTS % context.id)

        # Проверка корректности полей сохраняемого объекта
        result = self.parent.validate_row(obj, request)
        if result:
            assert isinstance(result, ActionResult)
            return result

        result = self.parent.save_row(obj)
        if isinstance(result, OperationResult) and result.success is True:
            # узкое место. после того, как мы переделаем работу экшенов,
            # имя параметра с идентификатором запси может уже называться не
            # id
            if 'm3_audit' in get_installed_apps():
                AuditManager().write(
                    'dict-changes',
                    user=request.user,
                    model_object=obj,
                    type='new' if not context.id else 'edit')
            context.id = obj.id
        return result


class ListDeleteRowAction(Action):
    url = '/delete_row$'

    def run(self, request, context):
        """
        Удаляться одновременно могут несколько объектов. Их ключи
        приходят разделенные запятыми.
        """
        ids = utils.extract_int_list(request, 'id')
        try:
            objs = [self.parent.get_row(id) for id in ids]
        except self.parent._nofound_exception:
            return OperationResult.by_message(MSG_DOESNOTEXISTS % id)

        result = self.parent.delete_row(objs)
        if (isinstance(result, OperationResult) and
                result.success is True and
                'm3_audit' in get_installed_apps()):
            for obj in objs:
                AuditManager().write(
                    'dict-changes',
                    user=request.user,
                    model_object=obj,
                    type='delete')
        return result


class DictCopyAction(Action):
    """
    Копирование записи из справочника
    """

    url = '/copy$'

    def context_declaration(self):
        return [
            ACD(
                name='id',
                type=int,
                required=True,
                verbose_name=u'id элемент справочника'
            )]

    def run(self, request, context):
        """
        """
        base = self.parent

        win = utils.bind_object_from_request_to_form(
            request, base.get_row, base.edit_window, exclusion=['id'])

        if not win.title:
            win.title = base.title

        win.form.url = base.save_action.get_absolute_url()
        # укажем адрес для чтения данных
        win.data_url = base.edit_window_action.get_absolute_url()
        win.orig_request = request
        win.orig_context = context

        # У окна может быть процедура доп. конфигурации
        # под конкретный справочник
        if (hasattr(win, 'configure_for_dictpack') and
                callable(win.configure_for_dictpack)):
            win.configure_for_dictpack(
                action=self, pack=self.parent,
                request=request, context=context)

        return ExtUIScriptResult(base.get_edit_window(win))


class BaseDictionaryActions(ActionPack, ISelectablePack):
    """
    Пакет с действиями, специфичными для работы со справочниками
    """
    # Заголовок окна справочника
    title = ''  # для записи
    # Список колонок состоящий из кортежей (имя json поля, имя колонки в окне)
    list_columns = []

    # Окно для редактирования элемента справочника:
    add_window = None  # Нового
    edit_window = None  # Уже существующего

    # Класс отвечающие за отображение форм:
    list_form = ExtDictionaryWindow  # Форма списка
    select_form = ExtDictionaryWindow  # Форма выбора

    # Настройки секретности. Если стоит истина,
    # то в результат добавляется флаг секретности
    secret_json = False
    secret_form = False

    # Ширина и высота окна
    width, height = 510, 400

    list_paging = True
    list_readonly = False

    # Значение колонки по-умолчанию, которое будет подбираться
    # при выборе значения из справочника
    column_name_on_select = 'name'

    # Добавлена ли возможность копирования
    allow_copy = False

    # права доступа для базовых справочников
    PERM_EDIT = 'edit'
    sub_permissions = {PERM_EDIT: u'Редактирование справочника'}

    def __init__(self):
        super(BaseDictionaryActions, self).__init__()
        # В отличие от обычных паков в этом экшены
        # создаются самостоятельно, а не контроллером
        # Чтобы было удобно обращаться к ним по имени
        self.list_window_action = DictListWindowAction()
        self.select_window_action = DictSelectWindowAction()
        self.edit_window_action = DictEditWindowAction()
        self.rows_action = DictRowsAction()
        self.last_used_action = DictLastUsedAction()
        self.row_action = ListGetRowAction()
        self.save_action = DictSaveAction()
        self.delete_action = ListDeleteRowAction()
        self.copy_action = DictCopyAction()
        # Но привязать их все равно нужно
        self.actions = [
            self.list_window_action,
            self.select_window_action,
            self.edit_window_action,
            self.rows_action,
            self.last_used_action,
            self.row_action,
            self.save_action,
            self.delete_action,
            self.copy_action
        ]

        # Исключение перехватываемое в экшенах, если объект не найден
        self._nofound_exception = ObjectNotFound

    # ==================== ФУНКЦИИ ВОЗВРАЩАЮЩИЕ АДРЕСА =====================
    def get_list_url(self):
        """
        Возвращает адрес формы списка элементов справочника.
        Используется для присвоения адресов в прикладном приложении.
        """
        return self.list_window_action.get_absolute_url()

    # ISelectablePack
    def get_select_url(self):
        """
        Возвращает адрес формы списка элементов справочника.
        Используется для присвоения адресов в прикладном приложении.
        """
        return self.select_window_action.get_absolute_url()

    # ISelectablePack
    def get_edit_url(self):
        """
        Возвращает адрес формы редактирования элемента справочника.
        """
        return self.edit_window_action.get_absolute_url()

    def get_rows_url(self):
        """
        Возвращает адрес по которому запрашиваются элементы грида
        """
        return self.rows_action.get_absolute_url()

    # ISelectablePack
    def get_autocomplete_url(self):
        """
        Возвращает адрес по которому запрашиваются элементы
        подходящие введенному в поле тексту
        """
        return self.get_rows_url()

    # ==================== ФУНКЦИИ ВОЗВРАЩАЮЩИЕ ДАННЫЕ =====================
    def get_rows(self, offset, limit, filter, user_sort=''):
        """
        Метод который возвращает записи грида в виде
        обычного питоновского списка.
        """
        raise NotImplementedError()

    def get_row(self, id):
        """
        Метод, который возвращает запись справочника
        с указанным идентификатором.
        """
        raise NotImplementedError()

    def get_last_used(self):
        """
        Метод, который возвращает список записей справочника,
        которые были выбраны конкретным пользователем в последнее время.
        Записи возвращаются в виде обычного питоновского списка.
        """
        raise NotImplementedError()

    def validate_row(self, obj, request):
        """
        Метод отвечает за проверку корректности полей сохраняемого объекта.
        Если все в порядке,
        то метод не возвращает ничего, иначе результат будет возвращен экшену.
        Т.е. вернуть можно любой из поддерживаемых в results.py объектов.
        """
        pass

    def save_row(self, obj):
        """
        Метод, который выполняет сохранение записи справочника.
        На момент запуска метода
        в параметре object находится именно та запись справочника,
        которую необходимо сохранить.
        """
        raise NotImplementedError()

    def delete_row(self, obj):
        """
        Метод, который выполняет удаление записи справочника.
        На момент запуска метода в
        параметре object находится именно та запись справочника,
        которую необходимо удалить.
        """
        raise NotImplementedError()

    # ISelectablePack
    def get_display_text(self, key, attr_name=None):
        """
        Получить отображаемое значение записи
        (или атрибута attr_name) по ключу key
        """
        row = self.get_row(key)
        if row is not None:
            name = attr_name if attr_name else self.column_name_on_select
            text = getattr(row, name)
            # getattr может возвращать метод, например verbose_name
            if callable(text):
                return text()
            else:
                return text

    # ISelectablePack
    def get_record(self, key):
        """
        Получить значение записи по ключу key
        """
        return self.get_row(key)

    # ====================== РАБОТА С ОКНАМИ ===============================
    def get_list_window(self, win):
        """
        Возвращает настроенное окно типа "Список" справочника
        """
        return win

    def get_select_window(self, win):
        """
        Возвращает настроенное окно выбора из справочника
        """
        return win

    def get_edit_window(self, win):
        ''' Возвращает настроенное окно редактирования элемента справочника '''
        return win


class BaseDictionaryModelActions(BaseDictionaryActions):
    """
    Класс, который реализует действия со справочником,
    записи которого являются моделями.
    """
    # Настройки вида справочника (задаются конечным разработчиком)
    model = None
    # Список полей модели по которым будет идти поиск
    filter_fields = []

    # Порядок сортировки элементов списка. Работает следующим образом:
    # 1. Если в list_columns модели списка есть поле
    # code, то устанавливается сортировка по возрастанию этого поля;
    # 2. Если в list_columns модели списка нет поля code, но
    # есть поле name, то устанавливается сортировка по возрастанию поля name;
    # Пример list_sort_order = ['code', '-name']
    list_sort_order = None

    def __init__(self):
        super(BaseDictionaryModelActions, self).__init__()
        if self.model:
            self._nofound_exception = self.model.DoesNotExist

    def get_rows_modified(
            self, offset, limit, filter,
            user_sort='', request=None, context=None):
        '''
        Возвращает данные для грида справочника
        '''
        if user_sort:
            sort_order = user_sort.split(',')
        else:
            sort_order = self.list_sort_order
        filter_fields = self._default_filter()
        query = self.model.objects.all()
        query = utils.apply_sort_order(query, self.list_columns, sort_order)
        query = utils.apply_search_filter(query, filter, filter_fields)
        if (hasattr(self, 'modify_rows_query') and
                callable(self.modify_rows_query)):
            query = self.modify_rows_query(query, request, context)
        total = query.count()
        if limit > 0:
            query = query[offset: offset + limit]
        result = {'rows': list(query), 'total': total}
        return result

    def get_rows(self, offset, limit, filter, user_sort=''):
        if user_sort:
            sort_order = user_sort.split(',')
        else:
            sort_order = self.list_sort_order
        filter_fields = self._default_filter()
        query = utils.apply_sort_order(
            self.model.objects, self.list_columns, sort_order)
        query = utils.apply_search_filter(query, filter, filter_fields)
        total = query.count()
        if limit > 0:
            query = query[offset: offset + limit]
        result = {'rows': list(query.all()), 'total': total}
        return result

    def get_row(self, id):
        assert isinstance(id, int)
        # Если id нет, значит нужно создать новый объект
        if id == 0:
            record = self.model()
        else:
            record = self.model.objects.get(id=id)
        return record

    @atomic
    def save_row(self, obj):
        obj.save()
        return OperationResult(success=True)

    def delete_row(self, objs):
        # Такая реализация обусловлена тем,
        # что IntegrityError невозможно отловить
        # до завершения транзакции, и приходится оборачивать транзакцию.
        @atomic
        def delete_row_in_transaction(self, objs):
            message = ''
            if len(objs) == 0:
                message = u'Элемент не существует в базе данных.'
            else:
                for obj in objs:
                    if (
                        isinstance(obj, BaseObjectModel) or
                        (
                            hasattr(obj, 'safe_delete') and
                            callable(obj.safe_delete)
                        )
                    ):
                        try:
                            obj.safe_delete()
                        except RelatedError as e:
                            message = e.args[0]
                    else:
                        if not safe_delete(obj):
                            message = (
                                u'Не удалось удалить элемент %s. '
                                u'Возможно на него есть ссылки.' % obj.id)
                            break
            return OperationResult.by_message(message)
        # Тут пытаемся поймать ошибку из транзакции.
        try:
            return delete_row_in_transaction(self, objs)
        except Exception as e:
            # Встроенный в Django IntegrityError
            # не генерируется. Кидаются исключения
            # специфичные для каждого драйвера БД.
            # Но по спецификации PEP 249 все они
            # называются IntegrityError
            if e.__class__.__name__ == 'IntegrityError':
                message = (
                    u'Не удалось удалить элемент. '
                    u'Возможно на него есть ссылки.')
                return OperationResult.by_message(message)
            else:
                # все левые ошибки выпускаем наверх
                raise

    def _default_filter(self):
        """
        Устанавливаем параметры поиска по умолчанию 'code' и 'name' в случае,
        если у модели есть такие поля
        """
        filter_fields = self.filter_fields[:]
        if not filter_fields:
            filter_fields.extend(
                [field.attname for field in self.model._meta.local_fields
                    if field.attname in ('code', 'name')])
        return filter_fields


class BaseEnumerateDictionary(BaseDictionaryActions):
    """
    Базовый экшен пак для построения справочников
    основанных на перечислениях, т.е.
    предопределенных неизменяемых наборах значений.
    """
    # Класс перечисление с которым работает справочник
    enumerate_class = None

    # Значений как правило мало и они влезают в одну страницу грида
    list_paging = False

    list_readonly = True
    list_columns = [('code', 'Код', 15),
                    ('name', 'Наименование')]

    def get_rows(self, offset, limit, filter, user_sort=''):
        """
        Возвращает данные для грида справочника
        """
        assert self.enumerate_class is not None, (
            'Attribute enumerate_class is not defined.')
        data = []
        for k, v in self.enumerate_class.values.items():
            if filter and v.upper().find(filter.upper()) < 0:
                continue
            else:
                data.append({'id': k, 'code': k, 'name': v})

        result = {'rows': data, 'total': len(data)}
        return result

    def get_row(self, id):
        """
        Заглушка для работы биндинга. В случае с перечислениями
        сам id хранится в БД
        """
        assert isinstance(id, int)
        assert id in self.enumerate_class.keys(), (
            'Enumarate key "%s" is not'
            ' defined in %s' % (id, self.enumerate_class))
        return id

    # ISelectablePack
    def get_display_text(self, key, attr_name=None):
        """
        Получить отображаемое значение записи
        (или атрибута attr_name) по ключу key
        """
        row_id = self.get_row(key)
        text = self.enumerate_class.values.get(row_id, '')
        return text

    # ISelectablePack
    def get_record(self, key):
        return self.get_row(key)
