# coding: utf-8
u"""Хелперы для отработки расширяемых конфигураций url'ов."""
from __future__ import absolute_import

from importlib import import_module
import collections
import inspect
import warnings

from m3 import caching
from m3.actions import Action
from m3.actions import ControllerCache
from m3_django_compat import get_installed_apps


def _get_instance(obj):
    if inspect.isclass(obj):
        warnings.warn(
            message=(
                "Возможность использования классов экшнов/паков"
                " вместо экземпляров, будет ликвидирована!"
            ),
            category=FutureWarning,
            stacklevel=2,
        )
        return obj()
    return obj


def get_app_urlpatterns():
    '''
    Возвращает конфигурацию урлов, объявленных в app_meta приложений.

    Данная функция не проглатывает ошибки, а выбрасывает все наружу.
    Перехват исключительных ситуаций данной фунции необходимо осуществлять
    вручную в urls.py прикладных приложений
    '''
    url_patterns = []

    for app_name in get_installed_apps():
        try:
            module = import_module('.app_meta', app_name)
        except ImportError as err:
            # по идее, такая ошибка возникает в
            # случае, если у нас для
            # установленного приложения
            # нет модуля app_meta.py
            if err.args[0].find('No module named') == -1:
                raise
            continue
        proc = getattr(module, 'register_urlpatterns', None)
        if callable(proc):
            url_patterns += proc()

    return url_patterns


def get_pack(pack_name):
    '''
    Получает экшенпак по имени
    '''
    pack_data = PacksNameCache().get(pack_name, None)
    return pack_data[0] if pack_data else None


def get_pack_instance(pack_name):
    '''
    Получает экземпляр набора экшенов по имени из контроллеров
    '''
    pack_data = PacksNameCache().get(pack_name, None)
    return pack_data[2] if pack_data else None


def get_action(action_name):
    '''
    Возвращает полный класс экшена, объявленного с указанным
    квалифицирующим именем.
    '''
    action_data = ActionsNameCache().get(action_name, None)
    return action_data[0] if action_data else None


def get_url(action):
    """
    Возвращает абсолютный путь до
    """
    names = []
    if isinstance(action, Action):
        # гениальных ход! - с чем пришли, то и ищем :)
        names.append(action.get_absolute_url())
    elif inspect.isclass(action) and issubclass(action, Action):
        names.append("%s.%s" % (action.__module__, action.__name__))
    elif isinstance(action, str):
        names.append(action)

    action_data = None
    for name in names:
        action_data = ActionsNameCache().get(name, None)
        if action_data:
            break
    return action_data[1] if action_data else ''


get_acton_url = get_url


def get_pack_url(pack_name):
    '''
    Возвращает абсолютный путь для набора экшенов
    '''
    pack_data = PacksNameCache().get(pack_name, None)
    return pack_data[1] if pack_data else ''


def get_pack_by_url(url):
    '''
    Возвращает набор экшенов по переданному url
    '''
    ControllerCache.populate()
    packs = collections.deque([])
    for controller in ControllerCache.get_controllers():
        packs.extend(controller.top_level_packs)

    while len(packs) > 0:
        pack = packs.popleft()
        if hasattr(pack, 'subpacks'):
            packs.extend(pack.subpacks)

        cleaned_pack = _get_instance(pack)
        if url == cleaned_pack.__class__.absolute_url():
            return cleaned_pack
    return None


# =============================================================================
# Кеш, используемый для хранения соответствия экшенов
# =============================================================================
class ActionsNameCache(caching.IntegralRuntimeCache):
    '''
    Кеш, используемый для хранения соответствия имен экшенов и паков
    соответствующим пакам
    '''

    def handler(self, cache, dimentions):
        '''
        Хендлер сборки кеша
        '''
        return inner_name_cache_handler(for_actions=True)


class PacksNameCache(caching.IntegralRuntimeCache):
    '''
    Кеш, используемый для хранения соответствия имен экшенов и паков
    соответствующим пакам
    '''

    def handler(self, cache, dimentions):
        '''
        Хендлер сборки кеша
        '''
        return inner_name_cache_handler(for_actions=False)


def inner_name_cache_handler(for_actions=True):
    '''
    Внутренний метод обхода дерева паков и экшенов.
    Используется в хендлерах сборки кешей
    '''
    def get_shortname(obj):
        '''
        Возвращает короткое имя для экшена или пака.
        Сам объект экшена или пака передается в параметре
        obj.
        '''
        names = ['shortname', 'short_name', ]
        objects = [obj.__class__, obj]
        for o in objects:
            for name in names:
                if hasattr(o, name) and isinstance(getattr(o, name), str):
                    return getattr(o, name, '')
        return ''

    # TODO посмотреть как работает для врапнутых классов
    result = {}

    # fullpaths - словарь, который хранит соответствие объекта (контроллера,
    # пака или экшена) и полного пути до него по шортнеймам
    fullpaths = {}

    ControllerCache.populate()
    # что-то внутренность данного метода не вызывает
    # доверия, если честно

    packs = collections.deque([])

    controllers = ControllerCache.get_controllers()

    # считываем паки верхнего уровня
    for controller in controllers:
        packs.extend(controller.top_level_packs)

        # добавляем полные пути в fullpaths
        fullpaths[controller] = get_shortname(controller)
        for pack in controller.top_level_packs:
            fullpaths[pack] = '%s.%s' % (
                fullpaths.get(controller, ''),
                get_shortname(pack)
            )

    while len(packs) > 0:
        pack = packs.popleft()
        # субпаки - в очередь!
        if hasattr(pack, 'subpacks'):
            packs.extend(pack.subpacks)

            for subpack in pack.subpacks:
                fullpaths[subpack] = '%s.%s' % (
                    fullpaths.get(pack, ''),
                    get_shortname(subpack),
                )

        if for_actions and hasattr(pack, 'actions'):
            for action in pack.actions:
                keys = []
                cache_object = None
                # если имеем дело с экземпляром
                # экшена, то ключем будет его
                # полный url
                if isinstance(action, Action):
                    cleaned_action = action
                    url = cleaned_action.get_absolute_url()
                    long_class_name = (
                        cleaned_action.__class__.__module__ + '.' +
                        cleaned_action.__class__.__name__
                    )
                    # регистрируем для url и для класса
                    cache_object = (
                        cleaned_action.__class__, url, cleaned_action)
                    keys.extend([url, long_class_name])
                else:
                    # неважно что нам передали, нам нужен экземпляр класса
                    cleaned_action = _get_instance(action)
                    # TODO: здесь url может быть не
                    # правильный, т.к. мы сами создали
                    # экземпляр и у него нет ни Pack, ни
                    # Controller.
                    # TODO: поэтому берем его через
                    # absolute_url, т.к. он ищет экземпляр
                    # экшена во всех контроллерах
                    # TODO: а это работает только для единичных экземпляров
                    url = cleaned_action.__class__.absolute_url()
                    long_class_name = (
                        cleaned_action.__class__.__module__ + '.' +
                        cleaned_action.__class__.__name__
                    )
                    # регистрируем для класса
                    cache_object = (
                        cleaned_action.__class__, url, cleaned_action)
                    keys.append(long_class_name)

                # регистрируем для shortname
                shortname = get_shortname(cleaned_action)
                if shortname:
                    keys.append(shortname)
                    keys.append('%s.%s' % (fullpaths.get(pack, ''), shortname))

                # регистрируем
                for key in keys:
                    result[key] = cache_object
        else:
            cleaned_pack = _get_instance(pack)
            url = cleaned_pack.__class__.absolute_url()
            cache_object = (cleaned_pack.__class__, url, cleaned_pack)
            # регистрируем как полный класс с модулем, так и просто имя класса
            keys = [
                '%s.%s' % (
                    cleaned_pack.__class__.__module__,
                    cleaned_pack.__class__.__name__
                ),
                cleaned_pack.__class__.__name__
            ]
            # регистрируем shortname
            shortname = get_shortname(cleaned_pack)
            if shortname:
                keys.append(shortname)
                if pack in fullpaths:
                    keys.append(fullpaths[pack])
            # регистрируем
            for key in keys:
                result[key] = cache_object

    return result
