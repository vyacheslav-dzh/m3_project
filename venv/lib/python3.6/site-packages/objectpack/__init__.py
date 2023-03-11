# coding: utf-8
u"""Библиотека для быстрой разработки простых справочников."""
import re

from django.utils.functional import cached_property

from objectpack.tools import escape_js_regex


class IMaskRegexField(object):
    u"""Класс-интерфейс для полей с маской ввода"""
    # строка или None
    _mask_re = None

    def __new__(cls, *args, **kwargs):
        if cls._mask_re is not None:
            # проверка того, что при проверке по данному паттерну обычная
            # строка не упадет с ошибой
            try:
                re.compile(cls._mask_re).match(u'sample')
            except TypeError:
                raise AssertionError((
                    u'Указанный _mask_re не может быть использован '
                    u'как регулярное выражение'), cls._mask_re)
        return super(IMaskRegexField, cls).__new__(cls)

    @cached_property
    def mask_re(self):
        u"""Свойство для атрибута mask_re поля формы. Опционально может
        задавать маску ввода
        :return:
        """
        if self._mask_re is not None:
            return escape_js_regex(self._mask_re)

    def set_mask_on_control(self, control):
        u"""Ставит регулярные выражения на поле.
        ВАЖНО: функция меняет передаваемый аргумент `control` в пользу
        быстродействия, поэтому её использование должно это учитывать
        ТАКЖЕ ВАЖНО: регулярное выражение рендерится в JS "как есть", в то
        время как синтаксис и поддерживаемые возможности немного различаются.
        К примеру, если регулярное выражение использует группы, то следует
        посмотреть на таблицу (
        http://web.archive.org/web/20130830063653/http://www.regular-expressions.info:80/refflavors.html)
        и проверить, что не используется недопустимое в JS выражение.
        Также для быстрого теста можно воспользоваться https://regex101.com/
        :param control: поле
        :return:
        """
        control.mask_re = self.mask_re
