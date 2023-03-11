# coding: utf-8
# pylint: disable=unused-import
from django import VERSION


_VERSION = VERSION[:2]


if _VERSION <= (1, 7):
    from django.contrib.contenttypes.generic import GenericForeignKey
else:
    from django.contrib.contenttypes.fields import GenericForeignKey
