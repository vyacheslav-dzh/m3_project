# coding:utf-8
from __future__ import absolute_import

from django.db.models import signals as model_signals


try:
    import mptt
except ImportError:
    mptt = None


def disable_mptt_signals(model):
    '''
    Метод выключения обработки сигналов обработки дерева
    '''
    # Начиная с версии 0.5 сигналов в MPTT больше нет
    if mptt.VERSION < (0, 5):
        from mptt import signals as mptt_signals
        model_signals.pre_save.disconnect(
            receiver=mptt_signals.pre_save, sender=model)


def enable_mptt_signals(model):
    '''
    Метод включения обработки сигналов обработки дерева
    '''
    if mptt.VERSION < (0, 5):
        from mptt import signals as mptt_signals
        model_signals.pre_save.connect(
            receiver=mptt_signals.pre_save, sender=model)


def rebuild_mptt_tree(
        model, manage_mptt_signals=True, query_manager='objects'):
    """
    Метод пересчета атрибутов MPTT-модели. Может использоваться:
    - при включении новой модели в MPTT
    - при массовой загрузке данных

    Параметры:
        model - сама пересчитываемая модель
        manage_mptt_signals - признак выполнения отключения/включения
            сигналов MPTT для обработки дерева,
            иначе их надо отключать/включать вручную в вызывающем методе
        query_manager - вместо штатного менеджера запросов objects
            можно использовать собственный
    """

    import warnings
    warnings.warn(
        (
            'Use MyObj.tree.rebuild() instead '
            'http://django-mptt.github.com/django-mptt/mptt.managers'
            '.html?highlight=rebuild#mptt.managers.TreeManager.rebuild'
        ),
        DeprecationWarning,
        stacklevel=2
    )
    model_manager = getattr(model, query_manager)

    def build_node(model, opts, parent_id, tree_id, left, level):
        qs = model_manager.filter(**{opts.parent_attr: parent_id})
        right = left
        for node in qs:
            setattr(node, opts.tree_id_attr, tree_id)
            setattr(node, opts.left_attr, right+1)
            setattr(node, opts.level_attr, level+1)
            right = build_node(model, opts, node.pk, tree_id, right+1, level+1)
            setattr(node, opts.right_attr, right)
            node.save()
        right += 1
        return right

    if manage_mptt_signals:
        disable_mptt_signals(model)

    opts = model._meta
    qs = model_manager.filter(**{'%s__isnull' % opts.parent_attr: True})
    tree_id = 1
    for node in qs:
        setattr(node, opts.tree_id_attr, tree_id)
        setattr(node, opts.left_attr, 1)
        setattr(node, opts.level_attr, 0)
        right = build_node(model, opts, node.pk, tree_id, 1, 0)
        setattr(node, opts.right_attr, right)
        node.save()
        tree_id += 1

    if manage_mptt_signals:
        enable_mptt_signals(model)
