from objectpack.ui import BaseEditWindow, make_combo_box
from m3_ext.ui import all_components as ext
from .models import ContentType, Permission, User
import datetime

class UserAddWindow(BaseEditWindow):

    def _init_components(self):
        """
        Здесь следует инициализировать компоненты окна и складывать их в
        :attr:`self`.
        """
        super(UserAddWindow, self)._init_components()

        self.field__password = ext.ExtStringField(
            label=u'password',
            name='password',
            allow_blank=False,
            anchor='100%')

        self.field__last_login = ext.ExtDateField(
            label=u'last login',
            name='last_login',
            anchor='100%',
            format='d.m.Y')

        self.field__superuser_status = ext.ExtCheckBox(
            label=u'superuser status',
            name='superuser_status',
            anchor='100%')

        self.field__username = ext.ExtStringField(
            label=u'username',
            name='username',
            allow_blank=False,
            anchor='100%')

        self.field__first_name = ext.ExtStringField(
            label=u'first name',
            name='first_name',
            anchor='100%')

        self.field__last_name = ext.ExtStringField(
            label=u'last name',
            name='last_name',
            anchor='100%')

        self.field__email = ext.ExtStringField(
            label=u'email address',
            name='email',
            anchor='100%')
        
        self.field__staff_status = ext.ExtCheckBox(
            label=u'staff status',
            name='staff_status',
            anchor='100%')

        self.field__active = ext.ExtCheckBox(
            label=u'active',
            name='active',
            anchor='100%',
            checked=True)

        self.field__date_joined = ext.ExtDateField(
            label=u'date joined',
            name='date_joined',
            anchor='100%',
            format='d.m.Y',
            value=datetime.datetime.now())
        

    def _do_layout(self):
        """
        Здесь размещаем компоненты в окне
        """
        super(UserAddWindow, self)._do_layout()
        self.form.items.extend((
            self.field__password,
            self.field__last_login,
            self.field__superuser_status,
            self.field__username,
            self.field__first_name,
            self.field__last_name,
            self.field__email,
            self.field__staff_status,
            self.field__active,
            self.field__date_joined,
        ))

    def set_params(self, params):
        """
        Установка параметров окна

        :params: Словарь с параметрами, передается из пака
        """
        super(UserAddWindow, self).set_params(params)
        self.height = 'auto'


class PermissionAddWindow(BaseEditWindow):

    def _init_components(self):
        """
        Здесь следует инициализировать компоненты окна и складывать их в
        :attr:`self`.
        """
        super(PermissionAddWindow, self)._init_components()

        self.field__name = ext.ExtStringField(
            label=u'name',
            name='name',
            allow_blank=False,
            anchor='100%')

        ctype = ContentType.objects.all()
        #ctypes = [{'ct':ct, 'name':ct.model} for ct in ctype]
        ctypes = [[ct.id, ct.model] for ct in ctype]
        # store = ext.ExtDataStore({
        #     'fields': ['ct', 'name'],
        #     'data': [{'ct': ct, 'name': ct.model} for ct in ctypes]
        # })

        self.field__content_type = make_combo_box(
            label=u'content type',
            name='content_type',
            anchor='100%',
            data=ctypes,
            )
        
        # self.field__content_type.set_store(store)
        # self.field__content_type.value_field = 'ct'
        # self.field__content_type.display_field = 'data'
        print(self.field__content_type.data)

        self.field__codename = ext.ExtStringField(
            label=u'codename',
            name='codename',
            anchor='100%')
        

    def _do_layout(self):
        """
        Здесь размещаем компоненты в окне
        """
        super(PermissionAddWindow, self)._do_layout()
        self.form.items.extend((
            self.field__name,
            self.field__content_type,
            self.field__codename,
        ))

    def set_params(self, params):
        """
        Установка параметров окна

        :params: Словарь с параметрами, передается из пака
        """
        super(PermissionAddWindow, self).set_params(params)
        self.height = 'auto'