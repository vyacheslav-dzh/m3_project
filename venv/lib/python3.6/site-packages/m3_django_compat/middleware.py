# coding: utf-8
# pylint: disable=unused-import
from __future__ import unicode_literals


# Примесь для промежуточных слоев, переход от MIDDLEWARE_CLASSES к MIDDLEWARE
try:
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    MiddlewareMixin = object
