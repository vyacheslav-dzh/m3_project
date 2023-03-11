function isGridSelected(grid, title, message){
    res = true;
    if (!grid.getSelectionModel().hasSelection() ) {
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
    var id, displayText, additionalData = {};

    var grid = Ext.getCmp('{{ component.grid.client_id}}');
    if (!isGridSelected(grid, 'Выбор элемента', 'Выберите элемент из списка') ) {
        return;
    }
    
    {% if component.multi_select %}
        var selections = grid.selModel.getSelections(),
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
        var selected = grid.getSelectionModel().getSelected();
        id = selected.id;
        displayText = selected.get("{{ component.column_name_on_select }}");
        // {# Формирует словарь дополнительных данных для передачи в обработчик afterselect #}
        var keysForData = JSON.parse('{{ component.additional_data_names|escapejs }}'.replace(/'/gi, '"'));
        for (var i = 0; i < keysForData.length; i++){
            var key = keysForData[i];
            var data = selected.get(key);
            additionalData[key] = data;
        }
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
            win.fireEvent('closed_ok', id, displayText, additionalData);
        };
        win.close();
    {% endif %}
}
