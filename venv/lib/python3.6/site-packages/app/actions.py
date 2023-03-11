from objectpack.actions import ObjectPack
from objectpack.ui import ModelEditWindow
from app import ui
from .models import ContentType, User, Group, Permission


class UserPack(ObjectPack):

    model = User
    add_window = edit_window = ui.UserAddWindow
    #add_window = edit_window = ModelEditWindow.fabricate(model)
    columns = [
        {
            'data_index': 'username',
            'header': u'Логин',
        },
        {
            'data_index': 'first_name',
            'header': u'Имя',
        },
        {
            'data_index': 'last_name',
            'header': u'Фамилия',
        },
        {
            'data_index': 'email',
            'header': u'Почта',
        }
    ]

    # разрешим добавлять ссылку на list_window в меню Desktop'а
    add_to_menu = True

class ContentTypePack(ObjectPack):
    model = ContentType
    add_window = edit_window = ModelEditWindow.fabricate(model)
    add_to_menu = True

class GroupPack(ObjectPack):
    model = Group
    add_window = edit_window = ModelEditWindow.fabricate(model)
    add_to_menu = True

class PermissionPack(ObjectPack):
    model = Permission
    add_window = edit_window = ui.PermissionAddWindow
    #add_window = edit_window = ModelEditWindow.fabricate(model, field_list=['name', 'ContentType', 'codename'],)
    add_to_menu = True

    # def save_row(self, obj, create_new, request, context):
    #     ctype = ContentType.objects.get_for_id(obj[0])
    #     super(PermissionPack, self).save_row(ctype, create_new, request, context)