# coding: utf-8
"""
Исключения, возникающие при работе контроллера
++++++++++++++++++++++++++++++++++++++++++++++
"""


class ApplicationLogicException(Exception):
    '''
    Исключительная ситуация уровня бизнес-логики приложения.
    '''

    def __init__(self, message):
        # Это исключение плохо подвергается pickle! пришлось дописать.
        # пруф:
        #   http://bugs.python.org/issue1692335
        #   http://bugs.python.org/issue13751
        Exception.__init__(self, message)
        self.exception_message = message

    def __str__(self):
        return self.exception_message


class ActionException(Exception):
    def __init__(self, clazz, *args, **kwargs):
        super(Exception, self).__init__(*args, **kwargs)
        self.clazz = clazz


class ActionNotFoundException(ActionException):
    """
    Возникает в случае, если экшен не найден ни в одном контроллере
    """
    def __str__(self):
        return 'Action "%s" not registered in controller/pack' % self.clazz


class ActionPackNotFoundException(ActionException):
    """
    Возникает в случае, если пак не найден ни в одном контроллере
    """
    def __str__(self):
        return 'ActionPack "%s" not registered in controller/pack' % self.clazz


class ReinitException(ActionException):
    """
    Возникает, если из-за неправильной структуры паков один и тот же
    экземпляр экшена может быть повторно инициализирован неверными значениями.
    """
    def __str__(self):
        return 'Trying to overwrite class "%s"' % self.clazz


class ActionUrlIsNotDefined(ActionException):
    """
    Возникает если в классе экшена не задан атрибут url.
    Это грозит тем, что контроллер не сможет найти и вызвать
    данный экшен при обработке запросов.
    """
    def __str__(self):
        return (
            'Attribute "url" is not defined '
            'or empty in action "%s"' % self.clazz
        )
