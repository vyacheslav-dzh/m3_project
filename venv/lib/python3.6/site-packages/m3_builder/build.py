# coding: utf-8
"""Модуль содержит класс, формирующий файл с информацией о сборке.

Класс BuildInfo при инстанцировании принимает парамет version_file_path,
содержащий директорию в которой будет храниться version.conf. При вызове
build.set_info() происходит определение параметров сборки и запись их
в файл version.conf. Если параметры сборки определить не удаётся, то
он пытается прочитать текущий version.conf пакета. Если он отсутствует,
то проставляются значение по-умолчанию. Класс используется в setup.py проекта.

Функция get_build_info принимает путь до файла version_file_path и
извлекает из него информацию о сборке. Эта информация, вместе с актуальным
окружением проекта возвращается в качестве ответа. Предполагается
использование функции для получения информации в окне "О системе".
"""
from __future__ import absolute_import
from __future__ import unicode_literals

from datetime import datetime
from functools import partial
from os.path import join
from os.path import normcase
from os.path import realpath
import codecs
import subprocess
import sys

from six.moves.configparser import ConfigParser
from six.moves.configparser import NoOptionError
from six.moves.configparser import NoSectionError
import pkg_resources


TEMPLATE = """
# Параметры этого файла управляют идентификацией версии проекта.
# Ручное изменение этих параметров приведет к невозможности
# нормальной установки обновлений.

[version]
BRANCH = {branch}
VERSION = {version}
REVISION = {revision}
VERSION_DATE = {version_date}
REVISION_DATE = {revision_date}
"""


class BuildInfo(object):

    """Предоставляет информацию о сборке.

    Информация извлекается из репозитария и окружения.
    Если же они не доступны, то из version.conf.

    Пример использования в ``setup.py``:

    .. code-block:: python

       setup(
           ...
           dependency_links=(
               'http://pypi.bars-open.ru/simple/m3-builder',
           ),
           setup_requires=(
               'm3-builder>=1.0.2',
           ),
           set_build_info=dirname(__file__),
       )
    """

    def __init__(self, version_file_path):
        """Инициализация экземпляра.

        :params str version_file_path: путь к файлу с информацией о версии.
            Значение по умолчанию ``'version.conf'``.
        """
        self._tag = self._branch = self._change_set = None
        self._version_file = join(version_file_path, 'version.conf')

        # Текущее содержимое файла version.conf
        self._current_version_cnf_content = get_build_info(version_file_path)

    @staticmethod
    def _exec(cmd_line):
        """Метод производит выполнение консольной команды."""
        command = subprocess.Popen(
            cmd_line.split(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = command.communicate()
        if err:
            raise SystemError('Error occurred: ' + err.decode('utf-8'))

        return (out and out.strip()).decode('utf-8')

    @property
    def branch(self):
        """Возвращает название ветки с которой выполняется сборка."""
        if not self._branch:
            try:
                branch = self._exec('git name-rev --name-only HEAD')
            except SystemError:
                branch = self._current_version_cnf_content['branch']

            if not branch:
                raise SystemError(('Branch not found'))

            self._branch = branch

        return self._branch

    @property
    def change_set(self):
        """Возвращает хэш коммита с которого выполняется сборка."""
        if not self._change_set:
            try:
                change_set = self._exec('git rev-parse HEAD')
            except SystemError:
                change_set = self._current_version_cnf_content['revision']

            if not change_set:
                raise SystemError('ChangeSet not found')

            self._change_set = change_set

        return self._change_set

    @property
    def tag(self):
        """Возвращает тэг."""
        if not self._tag:
            # метка не поставлена
            try:
                tag = self._exec('git describe --tags')
            except SystemError:
                tag = self._current_version_cnf_content['version']

            if not tag:
                raise SystemError('Tag not found!')

            # аннотированные тэги могут выводиться с доп. информацией
            # с символами ~ и ^
            tag = tag.split('^')[0].split('~')[0]

            # если тэг не на текущем коммите, то будет информация о количестве
            # последующих коммитов и хэш.
            # уберем хэш и добавим dev перед количеством.
            if '-' in tag and tag != '-':
                tag = '{}.dev{}'.format(*tag.split('-')[:-1])

            self._tag = tag

        # метка поставлена
        return self._tag

    @property
    def version(self):
        """Возвращает версию сборки."""
        ver = self.tag
        if ver == 'undefined':
            # если делаем билд(т.е. тег не указан), то в версию
            # вместо тега ставим <название ветки.ревизия[:10]>
            ver = self.branch + '.' + self.change_set[:10]

        return ver

    def _get_content(self):
        """Формирование содержимого файла."""
        now = datetime.now().strftime('%d.%m.%Y')

        return TEMPLATE.format(
            branch=self.branch,
            version=self.version,
            revision=self.change_set,
            version_date=now,
            revision_date=now,
        )

    def set_info(self):
        """Определяет параметры сборки и записывает в файл."""
        content = self._get_content()
        with codecs.open(self._version_file, 'w', 'utf-8') as outfile:
            outfile.write(content)
        print(
            "\nVersion was set to '%s'\n" % self.version
        )


def _get_installed_distributions():
    """Возвращает информацию об установленных в окружении пакетах.

    :rtype: list of :class:`~pkg_resources.Distribution`.
    """
    stdlib_pkgs = ('python', 'wsgiref')
    if sys.version_info >= (2, 7):
        stdlib_pkgs += ('argparse',)

    # pylint: disable=not-an-iterable
    return [
        dist
        for dist in pkg_resources.working_set
        if (
            normcase(realpath(dist.location)).startswith(sys.prefix) and
            dist.key not in stdlib_pkgs
        )
    ]


def get_build_info(version_conf_file_path):
    """Возвращает информацию о сборке проекта.

    :param version_conf_file_path: полный путь до файла с информацией о сборке.

    Пример использования:

    .. code-block:: python

       info = get_build_info('/path/to/version_conf_dir')
    """
    parser = ConfigParser(defaults={'branch': 'null'})
    version_conf_file = join(version_conf_file_path, 'version.conf')
    parser.read(version_conf_file)
    parser_get = partial(parser.get, 'version')

    try:
        data = {
            'version': parser_get('VERSION'),
            'revision': parser_get('REVISION'),
            'branch': parser_get('BRANCH'),
            'version_date': parser_get('VERSION_DATE'),
            'revision_date': parser_get('REVISION_DATE'),
        }
    except (NoSectionError, NoOptionError):
        data = dict.fromkeys((
            'version',
            'revision',
            'branch',
            'version_date',
            'revision_date',
        ), '-')

    data['environment'] = _get_installed_distributions()

    return data
