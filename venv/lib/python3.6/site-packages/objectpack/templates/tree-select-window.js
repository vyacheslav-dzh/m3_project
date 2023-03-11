{% include 'tree-list-window.js' %}

function isGridSelected(grid, title, message){
    res = true;
    if (!grid.getSelectionModel().getSelectedNode() ) {
        Ext.Msg.show({
           title: title,
           msg: message,
           buttons: Ext.Msg.OK,
           icon: Ext.MessageBox.INFO
        });
        res = false;
    };
    return res;
}

function selectValue(){
    var id, displayText;


    var grid = Ext.getCmp('{{ component.grid.client_id}}');
    if (!isGridSelected(grid, 'Выбор элемента', 'Выберите элемент из списка') ) {
        return;
    }
    
    {% if component.multi_select %}
        var selections = [grid.selModel.getSelectedNode(),],
            len = selections.length,
            ids = [],
        	displayTexts = [];
        for (var i = 0; i < len; i += 1) {
            ids.push(selections[i].id);
            displayTexts.push(selections[i].get("{{ component.column_name_on_select }}"));
        };
        id = ids.join(',');
        displayText = displayTexts.join(', ');
    {% else %}
        id = grid.getSelectionModel().getSelectedNode().id;
        displayText = grid.getSelectionModel().getSelectedNode().attributes.{{ component.column_name_on_select }};
    {% endif %}

    var win = Ext.getCmp('{{ component.client_id }}');
    {% if component.callback_url %}
        Ext.Ajax.request({
            url: "{{ component.callback_url }}"
            , success: function(res,opt) {
                result = Ext.util.JSON.decode(res.responseText)
                if (!result.success){
                    Ext.Msg.alert('Ошибка', result.message)
                }
                else {  
                    win.fireEvent('closed_ok');
                    win.close();
                }
            }
            ,params: Ext.applyIf({id: id}, {% if component.action_context %}{{component.action_context.json|safe}}{% else %}{}{% endif %})
            ,failure: function(response, opts){
                uiAjaxFailMessage();
            }
        });
    {% else %}
        if (id!=undefined && displayText!=undefined){
            win.fireEvent('select_value', id, displayText); // deprecated
            win.fireEvent('closed_ok', id, displayText); 
        };
        win.close();
    {% endif %}
}
