# coding:utf-8
from __future__ import absolute_import

import sys

from django.core.management.base import BaseCommand
from m3.actions import ControllerCache


class Command(BaseCommand):
    '''
    Запуск doctests для указанного модуля
    '''
    help = 'Run ControllerCache self-test'

    def handle(self, *args, **options):
        warns = ControllerCache._self_test()
        for warn in warns:
            sys.stderr.write('Self-test warning: %s\n' % warn)
