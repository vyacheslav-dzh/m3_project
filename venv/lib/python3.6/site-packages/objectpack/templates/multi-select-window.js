Ext.apply(win, {

    initMultiSelect:function(selectedItems) {
        var grid = Ext.getCmp('{{ component.grid.client_id }}');
        var mask = new Ext.LoadMask(this.body);
        var store = grid.getStore();
        this.checkedItems = this.extractSelectedData(selectedItems);
        this.mask = mask;
        this.grid = grid;
        this.allRecordsStore = new Ext.data.Store({
            model: store.model,
            recordType: store.recordType,
            proxy: store.proxy,
            reader: store.reader,
            sortInfo: store.sortInfo
        });
        this.isAllRecordsSelected = false;
        this.checkBoxSelectTooltip = "Отменить выбор записей на всех страницах";
        this.checkBoxDeSelectTooltip = "Выбрать записи на всех страницах";

        grid.on('headerclick', this.onChangeAllRecordsSelection, this);
        grid.getStore().on('load', this.onGridStoreLoad, this);
        grid.getSelectionModel().on('rowselect', this.onCheckBoxSelect, this);
        grid.getSelectionModel().on('rowdeselect', this.onCheckBoxDeselect, this);
        this.allRecordsStore.on('loadexception', function(){mask.hide();}, this);
        this.allRecordsStore.on('load', this.onAllStoreLoad, this);
    },

    onAllStoreLoad: function(store, records, options){
        var message=String.format(
            'Выбрано {0} элементов', store.getTotalCount());
        if (this.grid.allowPaging){
            pageCount = Math.ceil(
                store.getTotalCount()/this.grid.getBottomToolbar().pageSize
            );
            message += String.format(' на {0} страницах', pageCount);
        }

        store.each(function(record){
            win.checkedItems[record.id] = record;
        });
        Ext.Msg.show({title: 'Внимание', msg: message, buttons: Ext.Msg.OK,
            icon: Ext.MessageBox.INFO});
        this.mask.hide();
    },

    extractSelectedData:function(selectedItems) {
        var i = 0, result = {};
        for(; i < selectedItems.length; i++) {
            result[selectedItems[i].data.id] = selectedItems[i].copy();
        }
        return result;
    },

    onGridStoreLoad:function(store, records, options) {
        var i = 0, j = 0, recordsToSelect = [];
        var headerCell = this.grid.getView().getHeaderCell(0);
        for (;i< records.length;i++) {
            if (this.checkedItems[records[i].data.id]) {
                recordsToSelect.push(records[i]);
            }
        }
        headerCell.title = this.checkBoxDeSelectTooltip;
        this.grid.getSelectionModel().selectRecords(recordsToSelect);
        if (this.isAllRecordsSelected){
            this.selectAllRecordsCheckBox();
        }
    },

    onCheckBoxSelect:function(selModel, rowIndex, record) {
        if (!this.checkedItems[record.data.id] ) {
            this.checkedItems[record.data.id] = record.copy();
        }
    },

    onCheckBoxDeselect:function(selModel, rowIndex, record) {
        if (this.checkedItems[record.id]) {
            delete this.checkedItems[record.id];
            this.isAllRecordsSelected = false;
            this.deselectAllRecordsCheckBox();
        }
    },

    onChangeAllRecordsSelection:function(grid, columnIndex, event) {
        var headerCell = grid.getView().getHeaderCell(0);
        if (columnIndex != 0)
            return;
        if (this.isAllRecordsCheckBoxSelected()){
            this.selectAllRecords();
            this.isAllRecordsSelected = true;
            headerCell.firstChild.title = this.checkBoxSelectTooltip;
        } else {
            this.checkedItems = [];
            headerCell.firstChild.title = this.checkBoxDeSelectTooltip;
        }
    },

    deselectAllRecordsCheckBox:function(){
        var headerCell = this.grid.getView().getHeaderCell(0);
        headerCell.firstChild.classList.remove('x-grid3-hd-checker-on');
        headerCell.firstChild.title = this.checkBoxDeSelectTooltip;
    },

    selectAllRecordsCheckBox:function(){
        var headerCell = this.grid.getView().getHeaderCell(0);
        headerCell.firstChild.classList.add('x-grid3-hd-checker-on');
        headerCell.firstChild.title = this.checkBoxSelectTooltip;
    },

    isAllRecordsCheckBoxSelected:function(){
        var headerCell = this.grid.getView().getHeaderCell(0);
        return Array.from(
            headerCell.firstChild.classList.values()
        ).includes('x-grid3-hd-checker-on');
    },

    selectAllRecords: function(){
        this.allRecordsStore.baseParams = Ext.applyIf(
            {start: 0, limit:0},
            this.grid.getStore().baseParams
        );
        this.allRecordsStore.reload();
        this.mask.show();
    }
});


function isGridSelected(win, title, message){
    var res = true;
    if (Object.keys(win.checkedItems).length==0) {
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

function selectValue() {
    var records = [], win, v;
    win = Ext.getCmp('{{ component.client_id }}');
    var grid = Ext.getCmp('{{ component.grid.client_id }}');

    if (!isGridSelected(win, 'Выбор элемента', 'Выберите элемент из списка') ) {
        return;
    }

    for (v in win.checkedItems) {
        if (win.checkedItems.hasOwnProperty(v) && win.checkedItems[v] !== undefined) {
            records.push(win.checkedItems[v]);
        }
    };
    win = Ext.getCmp('{{ component.client_id}}');
    win.fireEvent('closed_ok', records);
    win.close();
};
