# coding: utf-8
from __future__ import absolute_import

import hashlib
import time

from six import text_type


DEFAULT_STATSD_HOST = 'localhost'
DEFAULT_STATSD_PORT = 8125


def create_statsd_client(settings):
    if getattr(settings, 'ENABLE_METRICS_COLLECTION', False):
        return StatsdClient(settings)
    else:
        return FakeStatsdClient()


class TimingManager(object):

    def __init__(self, client, controller, request):
        self._client = client
        self._controller = controller
        self._request = request

    def __enter__(self):
        self.time = time.time()
        return self

    def __exit__(self, ex, ex_cls, tb):
        self._client.process(self._controller, self._request, self.time)
        return ex is None


class StatsdClient(object):

    def get_hash(self, s):
        md5 = hashlib.md5()
        md5.update(s)
        str_hash = md5.hexdigest()
        return str_hash

    def __init__(self, settings):
        try:
            import pystatsd
        except ImportError as ie:
            raise ImportError(
                'Metrics collection is enabled, but we failed to '
                'import "pystatsd": {0}'.format(text_type(ie))
            )
        prefix = getattr(settings, 'METRICS_PREFIX', None)
        host = getattr(settings, 'METRICS_HOST', DEFAULT_STATSD_HOST)
        port = getattr(settings, 'METRICS_PORT', DEFAULT_STATSD_PORT)
        self._client = pystatsd.Client(host, port, prefix=prefix)

    def __call__(self, controller, request):
        return TimingManager(self, controller, request)

    def process(self, controller, request, time):
        controller_hash = self.get_hash(controller.url)
        url_hash = self.get_hash(request.path)

        prefix = 'controller_{0}.url_{1}.method_{2}'.format(
            controller_hash, url_hash, request.method.lower())

        self._client.incr('{0}.requests.count'.format(prefix))
        self._client.incr('requests.total')
        self._client.timing_since('{0}.requests.timing'.format(prefix), time)


class FakeStatsdClient(object):

    def __call__(self, controller, request):
        return TimingManager(self, controller, request)

    def process(self, *args, **kwargs):
        pass
