// основной блок для потомков
var win = Ext.getCmp('{{ component.client_id }}');

{% block content %}{% endblock %}

// подключение шаблонов вкладок
{% for t in component.tabs_templates %}
    {% include t %}
{% endfor %}
