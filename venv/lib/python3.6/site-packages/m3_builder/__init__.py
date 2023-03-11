# coding: utf-8
from __future__ import absolute_import
from __future__ import unicode_literals

from .build import BuildInfo


def set_build_info(dist, attr, value):
    """Заполняет, либо предоставляет, информацию о версии дистрибутива.

    Вызывается из :func:`setuptools.setup`.

    .. seealso::

       :class:`m3_builder.build.BuildInfo`
    """
    build_info = BuildInfo(value)
    build_info.set_info()
    dist.metadata.version = build_info.version
