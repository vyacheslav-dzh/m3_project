# coding:utf-8
u"""Вспомогательные функции используемые в паках."""
from __future__ import absolute_import

import json

from django.db import models
from django.db.models.query_utils import Q
from m3_django_compat import get_request_params
from six import integer_types
from six.moves import map
from six.moves import range

from .exceptions import ApplicationLogicException


def apply_sort_order(query, columns, sort_order):
    '''
    Закладывает на запрос порядок сортировки.
    Сначала если в описании колонок columns есть code,
    то сортируем по нему, иначе если есть по name.
    Если задан sort_order, то он главнее всех.

    :param query: запрос, к которому накладывается порядок
        сортировки
    :type query: django.db.models.query.QuerySet

    :param columns: список колонок
    :type columns: dict или tuple

    :param list sort_order: если не пустой, то сортирует по нему

    :return: запрос с наложенной сортировкой
    :rtype: django.db.models.query.QuerySet
    '''
    if isinstance(sort_order, list):
        query = query.order_by(*sort_order)
    else:
        order = None
        for column in columns:
            if isinstance(column, tuple):
                if column[0] == 'name':
                    order = 'name'
                elif column[0] == 'code':
                    order = 'code'
                    break
            elif isinstance(column, dict):
                name = column.get('data_index')
                if name == 'name':
                    order = 'name'
                elif name == 'code':
                    order = 'code'
                    break

        query = query.order_by(order) if order else query.all()
    return query


def create_search_filter(filter_text, fields):
    """
    Фильтрация производится по списку полей fields
    и введеному пользователем тексту filter_text.
    работает по следующей схеме
        Пример:
            fields = ['name', 'family']
            filter_text = u'Вася Пупкин'
        Получится условие WHERE:
            (name like '%Вася%' or family like '%Вася%') and
            (name like '%Пупкин%' or family like '%Пупкин%')
    если один из параметров пуст, то возвращает пустой Q()

    :param str filter_text: текст, пот которому ведется поиск

    :param list fields: список полей, по которым нужно искать

    :return: фильтр, реализующий поиск слов по полям
    :rtype: django.db.models.query_utils.Q
    """
    filter_all = Q()
    if filter_text and fields:
        words = filter_text.split()
        for word in words:
            filter_word = Q()
            for field in fields:
                filter_word |= Q(**{field + '__icontains': word})
            filter_all &= filter_word
    return filter_all


def apply_search_filter(query, filter, fields):
    '''
    Накладывает фильтр поиска на запрос.
    Вхождение каждого элемента фильтра ищется в заданных полях.
    @param query: django.db.models.query.QuerySet
    @param filter: Строка фильтра
    @param fields: Список полей модели по которым будет поиск
    '''
    assert isinstance(fields, (list, tuple))
    condition = create_search_filter(filter, fields)
    if condition is not None:
        query = query.filter(condition)
    return query


def detect_related_fields(query, list_columns):
    """
    Определяет необходимость выполнения select_related на запросе.

    :param query: запрос, к которому применяется select_related
    :type query: django.db.models.query.QuerySet


    """
    # Люди любят указывать в list_columns такие вещи как client.contragent
    # и вообще цеплять вложенные модели. Специально для них генерируем удобный
    # одиночный запрос, который выбирает все что нужно.
    related_models = []
    for column in list_columns:
        if isinstance(column, (tuple, list)):
            column_name = column[0]
        elif isinstance(column, str):
            column_name = column
        elif isinstance(column, dict):
            column_name = column.get('data_index')
            if not column_name:
                continue
        else:
            continue

        if '.' in column_name:
            column_fields = column_name.split('.')
            model_name = column_fields[0]

            # Считаем все что не является последним после точки моделью
            for deep_model in column_fields[1:-1]:
                model_name = model_name + '__' + deep_model

            related_models.append(model_name)

    new_query = query.select_related(*related_models)
    return new_query


def bind_object_from_request_to_form(
        request, obj_factory, form, request_id_name='id', exclusion=None):
    '''
    Функция извлекает объект из запроса по id,
    создает его экземпляр и биндит к форме

    :param request: Запрос от клиента содержащий id объекта
    :type request: django.http.Request

    :param obj_factory: Функция возвращающая объект по его id
    :type obj_factory: callable-object

    :param form: Класс формы к которому привязывается объект
    :type form:

    :param request_id_name: Имя параметра запроса соответствующего ID объекта
    :type request_id_name:

    '''
    exclusion = exclusion or []

    # Получаем объект по id
    id = extract_int(request, request_id_name)
    obj = obj_factory(id)
    if obj is None:
        raise ApplicationLogicException(
            'Такого элемента не существует, возможно он был удален.'
            ' Обновите содержимое.'
        )
    # Разница между новым и созданным объектов в том,
    # что у нового нет id или он пустой
    create_new = True
    if isinstance(obj, dict) and obj.get('id') is not None:
        create_new = False
    elif hasattr(obj, 'id') and getattr(obj, 'id') is not None:
        create_new = False
    # Устанавливаем параметры формы
    win = form(create_new=create_new)
    # Биндим объект к форме
    win.form.from_object(obj, exclusion)
    return win


def bind_request_form_to_object(
        request, obj_factory, form, request_id_name='id', exclusion=None):
    '''
    Функция создает объект по id в запросе
    и заполняет его атрибуты из данных пришедшей формы
    @param request:     Запрос от клиента содержащий id объекта
    @param obj_factory: Функция возвращающая объект по его id
    @param form:        Класс формы к которому привязывается объект
    @param request_id_name:   Имя параметра запроса соответствующего ID объекта
    '''
    exclusion = exclusion or []
    # Получаем наш объект по id и биндим форму к нему
    id = extract_int(request, request_id_name)
    obj = obj_factory(id)

    # Разница между новым и созданным объектов в том,
    # что у нового нет id или он пустой
    create_new = True
    if isinstance(obj, dict) and obj.get('id') is not None:
        create_new = False
    elif hasattr(obj, 'id') and getattr(obj, 'id') is not None:
        create_new = False

    # Создаем форму для биндинга к ней
    win = form(create_new=create_new)
    win.form.bind_to_request(request)
    win.form.to_object(obj, exclusion)

    # Может возникнуть трудноуловимая ошибка, когда в request был id=0 и
    # он присвоился obj.id. В БД не должно быть pk=0
    if not id:
        obj.id = None

    return obj


def safe_delete_record(model, id=None):
    '''
    Безопасное удаление записи в базе.
    В отличие от джанговского ORM не удаляет каскадно.
    Возвращает True в случае успеха, иначе False
    @deprecated нужно использовать BaseModel.safe_delete()
        или m3.db.safe_delete(obj)
    '''
    import warnings
    warnings.warn((
        'ui.actions.utils.safe_delete_record(Model, id)'
        'must by replaced with m3.db.safe_delete(obj)'),
        DeprecationWarning,
        stacklevel=2
    )

    from m3.db import safe_delete
    assert (isinstance(model, models.Model) or issubclass(model, models.Model))
    assert (isinstance(id, integer_types) or id is None)

    if isinstance(model, models.Model):
        obj = model
    else:
        try:
            obj = model.objects.get(pk=id)
        except model.DoesNotExist:
            # не нашли значит уже удалили
            return True
    return safe_delete(obj)


def fetch_search_tree(
        model_or_query, filter, branch_id=None, parent_field_name='parent'):
    '''
    По заданному фильтру filter и модели model
    формирует развернутое дерево с результатами поиска.
    Если filter пустой, то получается полностью развернутое дерево.
    '''
    # branch_id - это элемент ограничивающий дерево,
    # т.е. должны возвращаться только дочерние ему элементы
    # Сначала тупо получаем все узлы подходящие по фильтру
    assert isinstance(
        model_or_query,
        (models.query.QuerySet, models.Manager)
    ) or issubclass(model_or_query, models.Model)

    if isinstance(model_or_query, (models.query.QuerySet, models.Manager)):
        query = model_or_query
        model = model_or_query.model
    else:
        query = model_or_query.objects
        model = model_or_query
    if branch_id and hasattr(model, 'get_descendants'):
        branch_node = query.get(id=branch_id)
        nodes = branch_node.get_descendants().select_related(parent_field_name)
    else:
        nodes = query.all().select_related(parent_field_name)

    if filter:
        nodes = nodes.filter(filter)

    # Из каждого узла создаем полный путь до корня
    paths = []
    processed_nodes = set()
    for node in nodes:
        # Проверяем не входит ли наш узел в один из просчитанных путей
        if node in processed_nodes:
            continue

        path = [node]
        while getattr(node, parent_field_name):
            if branch_id and getattr(node, parent_field_name) == branch_node:
                break
            if node == getattr(node, parent_field_name):
                break
            node = getattr(node, parent_field_name)
            path.append(node)
            processed_nodes.add(node)
        # Первый элемент пути всегда корень
        path.reverse()
        paths.append(path)

    if len(paths) == 0:
        return []

    def create_one_tree(path):
        ''' Превращает путь в дерево с понимаемое ExtJS гридом '''
        tree = path[0]
        for i in range(1, len(path)):
            path[i - 1].children = [path[i]]
        return tree

    # Начальное дерево в удобоваримом для грида формате
    tree = [create_one_tree(paths[0])]

    # Слияние путей в одно дерево
    def merge(sub_tree, path_slice):
        try:
            index = sub_tree.index(path_slice[0])
        except ValueError:
            sub_tree.append(create_one_tree(path_slice))
        else:
            if hasattr(sub_tree[index], 'children'):
                merge(sub_tree[index].children, path_slice[1:])
            else:
                # Значит сливаемый путь оказался длиннее чем высота поддерева
                sub_tree[index].children = [create_one_tree(path_slice[1:])]

    for path in paths[1:]:
        merge(tree, path)

    def set_tree_attributes(sub_tree):
        '''
        Пробегает дерево и устанавливает узлам атрибуты expanded и leaf
        '''
        if not hasattr(sub_tree, 'children') or len(sub_tree.children) == 0:
            # не факт, что это лист,
            # т.к. мы лишь фильтровали дерево - нужно проверить
            if hasattr(sub_tree, 'is_leaf_node'):
                sub_tree.leaf = sub_tree.is_leaf_node()
            else:
                child_nodes = query.filter(
                    Q(**{parent_field_name: sub_tree.id}))
                if len(child_nodes) == 0:
                    sub_tree.leaf = True
        else:
            sub_tree.expanded = True
            for st in sub_tree.children:
                set_tree_attributes(st)

    for sub_tree in tree:
        set_tree_attributes(sub_tree)

    return tree


def extract_int(request, key):
    """
    Извлекает целое число из запроса

    :param str key: имя параметра

    :return: целочисленное значение параметра с заданным именем
    :rtype: int
    """
    try:
        value = get_request_params(request).get(key, None)
    except IOError:
        # В некоторых браузерах (предполагается что в ie) происходит следующие:
        # REQUEST читается и в какой-то момент связь прекращается
        # из-за того, что браузер разрывает соединение,
        # в следствии этого происходит ошибка
        # IOError: request data read error
        raise

    if value:
        # если по каким-то причинам пришло не число, а что-то другое
        try:
            value = int(value)
        except ValueError:
            raise ApplicationLogicException(
                'Произошла ошибка при конвертации.')
        return value
    else:
        return 0


def extract_int_list(request, key):
    ''' Извлекает список целых чисел из запроса
        ..note:: разделителем в строком значении должна быть запятая

    :param unicode key: имя параметра

    :return: список целочисленных значений из параметра с
        заданным именем
    :rtype: list
    '''
    value = get_request_params(request).get(key, '')
    values = list(map(int, value.split(',')))
    return values


def extract_list(request, key):
    data = request.POST.get(key)
    if data:
        obj = json.loads(data)
        if not isinstance(obj, list):
            obj = [obj]
        return obj
    return []


def apply_column_filter(query, request, map):
    '''
    Накладывает колоночный фильтр

    :param query: Запрос
    :type query: django.db.models.query.QuerySet

    :param request: Данные с клиента включающие фильтры

    :param map: карта связи фильтров в request и полей в объекте:
        ключ - поле объекта, значение - параметр фильтра, например:
            {'unit__name':'unit_ref_name'}
    '''
    assert isinstance(map, dict)
    cond = None
    for key, value in map.items():
        filter_word = get_request_params(request).get(value)
        if filter_word:
            filter_q = Q(**{key + '__icontains': filter_word})
            cond = filter_q if not cond else (cond & filter_q)
    if cond:
        query = query.filter(cond)
    return query


def replace_action(pack, action_attr_name, new_action):
    """Замена указанного экшена в паке.

    :param pack: Пак, в котором нужно произвести замену.
    :type pack: m3.actions.ActionPack

    :param str action_attr_name: Имя поля пака, в котором хранится ссылка на
        экшн, подлежащий замене.

    :param new_action: Новый экшн.
    :type new_action: m3.actions.Action
    """
    pack.actions.remove(getattr(pack, action_attr_name))
    setattr(pack, action_attr_name, new_action)
    pack.actions.append(new_action)