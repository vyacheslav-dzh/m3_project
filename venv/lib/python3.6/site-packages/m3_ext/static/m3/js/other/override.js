/**
 * Здесь нужно перегружать объекты и дополнять функционал.
 * Этот файл подключается последним.
 */

/*
 * В IE нет поддержки метода find.
 */

if (!Array.prototype.find) {
    Object.defineProperty(Array.prototype, 'find', {
        value: function (predicate) {
            if (this == null) {
                throw new TypeError('"this" is null or not defined');
            }
            var o = Object(this);
            var len = o.length >>> 0;
            if (typeof predicate !== 'function') {
                throw new TypeError('predicate must be a function');
            }
            var thisArg = arguments[1];
            var k = 0;
            while (k < len) {
                var kValue = o[k];
                if (predicate.call(thisArg, kValue, k, o)) {
                    return kValue;
                }
                k++;
            }
            return undefined;
        }
    });
}
/**
 * Реализация bind, если таковой нет (для поддержки в IE)
 */
if (!Function.prototype.bind) {
  Function.prototype.bind = function(oThis) {
    if (typeof this !== 'function') {
      // closest thing possible to the ECMAScript 5
      // internal IsCallable function
      throw new TypeError('Function.prototype.bind - what is trying to be bound is not callable');
    }

    var aArgs   = Array.prototype.slice.call(arguments, 1),
        fToBind = this,
        fNOP    = function() {},
        fBound  = function() {
          return fToBind.apply(this instanceof fNOP && oThis
                 ? this
                 : oThis,
                 aArgs.concat(Array.prototype.slice.call(arguments)));
        };

    fNOP.prototype = this.prototype;
    fBound.prototype = new fNOP();

    return fBound;
  };
}

/**
 * Фикс компонента ComboBox.
 * Возвращалось имя функции, если в качестве значения оператор "with" принимал тип array.
 * 
 * https://developer.mozilla.org/ru/docs/Web/JavaScript/Reference/Statements/with
 */
Ext.XTemplate = function(){
    Ext.XTemplate.superclass.constructor.apply(this, arguments);

    var me = this,
        s = me.html,
        re = /<tpl\b[^>]*>((?:(?=([^<]+))\2|<(?!tpl\b[^>]*>))*?)<\/tpl>/,
        nameRe = /^<tpl\b[^>]*?for="(.*?)"/,
        ifRe = /^<tpl\b[^>]*?if="(.*?)"/,
        execRe = /^<tpl\b[^>]*?exec="(.*?)"/,
        m,
        id = 0,
        tpls = [],
        VALUES = 'xvalues',
        PARENT = 'parent',
        XINDEX = 'xindex',
        XCOUNT = 'xcount',
        RETURN = 'return ',
        WITHVALUES = 'with(xvalues){ ';

    s = ["'<tpl>", s, "</tpl>'"].join('');

    while((m = s.match(re))){
        var m2 = m[0].match(nameRe),
            m3 = m[0].match(ifRe),
            m4 = m[0].match(execRe),
            exp = null,
            fn = null,
            exec = null,
            name = m2 && m2[1] ? m2[1] : '';

       if (m3) {
           exp = m3 && m3[1] ? m3[1] : null;
           if(exp){
               fn = new Function(VALUES, PARENT, XINDEX, XCOUNT, WITHVALUES + RETURN +(Ext.util.Format.htmlDecode(exp))+'; }');
           }
       }
       if (m4) {
           exp = m4 && m4[1] ? m4[1] : null;
           if(exp){
               exec = new Function(VALUES, PARENT, XINDEX, XCOUNT, WITHVALUES +(Ext.util.Format.htmlDecode(exp))+'; }');
           }
       }
       if(name){
           switch(name){
               case '.': name = new Function(VALUES, PARENT, WITHVALUES + RETURN + VALUES + '; }'); break;
               case '..': name = new Function(VALUES, PARENT, WITHVALUES + RETURN + PARENT + '; }'); break;
               default: name = new Function(VALUES, PARENT, WITHVALUES + RETURN + name + '; }');
           }
       }
       tpls.push({
            id: id,
            target: name,
            exec: exec,
            test: fn,
            body: m[1]||''
        });
       s = s.replace(m[0], '{xtpl'+ id + '}');
       ++id;
    }
    for(var i = tpls.length-1; i >= 0; --i){
        me.compileTpl(tpls[i]);
    }
    me.master = tpls[tpls.length-1];
    me.tpls = tpls;
};
Ext.extend(Ext.XTemplate, Ext.Template, {
    re : /\{([\w\-\.\#]+)(?:\:([\w\.]*)(?:\((.*?)?\))?)?(\s?[\+\-\*\\]\s?[\d\.\+\-\*\\\(\)]+)?\}/g,
    codeRe : /\{\[((?:\\\]|.|\n)*?)\]\}/g,

    applySubTemplate : function(id, values, parent, xindex, xcount){
        var me = this,
            len,
            t = me.tpls[id],
            vs,
            buf = [];
        if ((t.test && !t.test.call(me, values, parent, xindex, xcount)) ||
            (t.exec && t.exec.call(me, values, parent, xindex, xcount))) {
            return '';
        }

        vs = t.target ? t.target.call(me, values, parent) : values;
        len = vs.length;
        parent = t.target ? values : parent;
        if(t.target && Ext.isArray(vs)){
            for(var i = 0, len = vs.length; i < len; i++){
                buf[buf.length] = t.compiled.call(me, vs[i], parent, i+1, len);
            }
            return buf.join('');
        }
        return t.compiled.call(me, vs, parent, xindex, xcount);
    },
    compileTpl : function(tpl){
        var fm = Ext.util.Format,
            useF = this.disableFormats !== true,
            sep = Ext.isGecko ? "+" : ",",
            body;

        function fn(m, name, format, args, math){
            if(name.substr(0, 4) === 'xtpl'){
                return "'"+ sep +'this.applySubTemplate('+name.substr(4)+', values, parent, xindex, xcount)'+sep+"'";
            }
            var v;
            if(name === '.'){
                v = 'values';
            }else if(name === '#'){
                v = 'xindex';
            }else if(name.indexOf('.') !== -1){
                v = name;
            }else{
                v = "values['" + name + "']";
            }
            if(math){
                v = '(' + v + math + ')';
            }
            if (format && useF) {
                args = args ? ',' + args : "";
                if(format.substr(0, 5) !== "this."){
                    format = "fm." + format + '(';
                }else{
                    format = 'this.call("'+ format.substr(5) + '", ';
                    args = ", values";
                }
            } else {
                args= ''; format = "("+v+" === undefined ? '' : ";
            }
            return "'"+ sep + format + v + args + ")"+sep+"'";
        }

        function codeFn(m, code){
            return "'" + sep + '(' + code.replace(/\\'/g, "'") + ')' + sep + "'";
        }

        if (Ext.isGecko) {
            body = "arguments[0].compiled = function(values, parent, xindex, xcount){ return '" +
                   tpl.body.replace(/(\r\n|\n)/g, '\\n').replace(/'/g, "\\'").replace(this.re, fn).replace(this.codeRe, codeFn) +
                    "';};";
        } else {
            body = ["arguments[0].compiled = function(values, parent, xindex, xcount){ return ['"];
            body.push(tpl.body.replace(/(\r\n|\n)/g, '\\n').replace(/'/g, "\\'").replace(this.re, fn).replace(this.codeRe, codeFn));
            body.push("'].join('');};");
            body = body.join('');
        }
        eval(body);
        return this;
    },
    applyTemplate : function(values){
        return this.master.compiled.call(this, values, {}, 1, 1);
    },
    compile : function(){return this;}
});
Ext.XTemplate.prototype.apply = Ext.XTemplate.prototype.applyTemplate;

/**
 * Необходимо для фикса ошибки "parentNode null or not an object" в IE10
 */
Ext.override(Ext.Element, {

    /**
    * Inserts this element after the passed element in the DOM
    * @param {Mixed} el The element to insert after
    * @return {Ext.Element} this
    *
    * @overrides  to fix IE issue of "parentNode null or not an object".
    */
    insertAfter: function(el){
        el = Ext.getDom(el);
        if (el && el.parentNode) {
            el.parentNode.insertBefore(this.dom, el.nextSibling);
        }
        return this;
    }
});


/**
 * Нужно для правильной работы окна
 */
Ext.onReady(function () {
    Ext.override(Ext.Window, {

        /*
         *  Если установлена модальность и есть родительское окно, то
         *  флаг модальности помещается во временную переменную tmpModal, и
         *  this.modal = false;
         */
        tmpModal: false,
        manager: new Ext.WindowGroup(),
        // 2011.01.14 kirov
        // убрал, т.к. совместно с desktop.js это представляет собой гремучую смесь
        // кому нужно - пусть прописывает Ext.getBody() в своем "десктопе" на onReady или когда хочет
        //,renderTo: Ext.getBody().id
        constrain: true,
        /**
         * Выводит окно на передний план
         * Вызывается в контексте дочернего
         * по отношению к parentWindow окну
         */
        activateChildWindow: function () {
            this.toFront();
        },
        listeners: {

            'beforeshow': function () {
                var renderTo = Ext.get(this.renderTo);
                if (renderTo) {
                    if (renderTo.getHeight() < this.getHeight())
                        this.setHeight(renderTo.getHeight());
                }

                if (this.parentWindow) {

                    this.parentWindow.setDisabled(true);

                    /*
                     * В Extjs 3.3 Добавили общую проверку в функцию mask, см:
                     *  if (!(/^body/i.test(dom.tagName) && me.getStyle('position') == 'static')) {
                     me.addClass(XMASKEDRELATIVE);
                     }
                     *
                     * было до версии 3.3:
                     *  if(!/^body/i.test(dom.tagName) && me.getStyle('position') == 'static'){
                     me.addClass(XMASKEDRELATIVE);
                     }
                     * Теперь же расположение замаскированых окон должно быть относительным
                     * (relative) друг друга
                     *
                     * Такое поведение нам не подходит и другого решения найдено не было.
                     * Кроме как удалять данный класс
                     * */
                    if (this.parentWindow.el) {
                        this.parentWindow.el.removeClass('x-masked-relative');
                    }

                    this.parentWindow.on('activate', this.activateChildWindow, this);

                    this.modal = false;
                    this.tmpModal = true;

                    if (window.AppDesktop) {
                        var el = AppDesktop.getDesktop().taskbar.tbPanel.getTabWin(this.parentWindow);
                        if (el) {
                            el.mask();
                        }
                    }
                }
                if (this.modal) {
                    var taskbar = Ext.get('ux-taskbar');
                    if (taskbar) {
                        taskbar.mask();
                    }
                    var toptoolbar = Ext.get('ux-toptoolbar');
                    if (toptoolbar) {
                        toptoolbar.mask();
                    }
                }
            },
            close: function () {
                if (this.tmpModal && this.parentWindow) {
                    this.parentWindow.un('activate', this.activateChildWindow, this);
                    this.parentWindow.setDisabled(false);
                    if (typeof this.parentWindow.toFront === 'function') {
                        this.parentWindow.toFront();
                    }

                    if (window.AppDesktop) {
                        var el = AppDesktop.getDesktop().taskbar.tbPanel.getTabWin(this.parentWindow);
                        if (el) {
                            el.unmask();
                        }
                    }
                }

                if (this.modal) {
                    var taskbar = Ext.get('ux-taskbar');
                    if (taskbar) {
                        taskbar.unmask();
                    }
                    var toptoolbar = Ext.get('ux-toptoolbar');
                    if (toptoolbar) {
                        toptoolbar.unmask();
                    }
                }
            },
            hide: function () {
                if (this.modal) {
                    if (!this.parentWindow) {
                        var taskbar = Ext.get('ux-taskbar');
                        if (taskbar) {
                            taskbar.unmask();
                        }
                        var toptoolbar = Ext.get('ux-toptoolbar');
                        if (toptoolbar) {
                            toptoolbar.unmask();
                        }
                    }
                }
            }
        }
    });
});

/**
 * Обновим TreeGrid чтобы колонки занимали всю ширину дерева
 */
Ext.override(Ext.ux.tree.TreeGrid, {

    // добавлено
    fitColumns: function () {
        var nNewTotalWidth = this.getInnerWidth() - Ext.num(this.scrollOffset, Ext.getScrollBarWidth());
        var nOldTotalWidth = this.getTotalColumnWidth();
        var cs = this.getVisibleColumns();
        var n, nUsed = 0;

        for (n = 0; n < cs.length; n++) {
            if (n == cs.length - 1) {
                cs[n].width = nNewTotalWidth - nUsed - 1;
                break;
            }
            cs[n].width = Math.floor((nNewTotalWidth / 100) * (cs[n].width * 100 / nOldTotalWidth)) - 1;
            nUsed += cs[n].width;
        }

        this.updateColumnWidths();
    },
    // <--
    onResize: function (w, h) {
        Ext.ux.tree.TreeGrid.superclass.onResize.apply(this, arguments);

        var bd = this.innerBody.dom;
        var hd = this.innerHd.dom;

        if (!bd) {
            return;
        }

        if (Ext.isNumber(h)) {
            bd.style.height = this.body.getHeight(true) - hd.offsetHeight + 'px';
        }

        if (Ext.isNumber(w)) {
            var sw = Ext.num(this.scrollOffset, Ext.getScrollBarWidth());
            if (this.reserveScrollOffset || ((bd.offsetWidth - bd.clientWidth) > 10)) {
                this.setScrollOffset(sw);
            } else {
                var me = this;
                setTimeout(function () {
                    me.setScrollOffset(bd.offsetWidth - bd.clientWidth > 10 ? sw : 0);
                }, 10);
            }
        }
        this.fitColumns(); // добавилась/заменила
    }
});

Ext.override(Ext.tree.ColumnResizer, {
    onEnd: function () {
        var nw = this.proxy.getWidth(),
            tree = this.tree;

        this.proxy.remove();
        delete this.dragHd;

        tree.columns[this.hdIndex].width = nw;
        //tree.updateColumnWidths(); // закомментировано
        tree.fitColumns();			// добавлено

        setTimeout(function () {
            tree.headersDisabled = false;
        }, 100);
    }
});

/**
 * Обновим ячейку дерева чтобы при двойном клике не открывались/сворачивались дочерние узлы
 */
Ext.override(Ext.tree.TreeNodeUI, {
    onDblClick: function (e) {
        e.preventDefault();
        if (this.disabled) {
            return;
        }
        if (this.fireEvent("beforedblclick", this.node, e) !== false) {
            if (this.checkbox) {
                this.toggleCheck();
            }
            // закомментировано.
            //if(!this.animating && this.node.isExpandable()){
            //    this.node.toggle();
            //}
            this.fireEvent("dblclick", this.node, e);
        }
    }
});
/**
 * Исправим ошибку, когда значения emptyText в композитном поле передаются на сервер,
 * даже если установлен признак "не передавать"
 */
Ext.override(Ext.form.Action.Submit, {
    run : function(){
        var o = this.options,
            method = this.getMethod(),
            isGet = method == 'GET';
        if(o.clientValidation === false || this.form.isValid()){
            if (o.submitEmptyText === false) {
                var fields = this.form.items,
                    emptyFields = [],
                    setupEmptyFields = function(f){
                        // M prefer: field (например, combobox) может быть неотрендеренный
                        if (f.rendered && f.el.getValue() == f.emptyText) {
                        // if (f.el.getValue() == f.emptyText) {
                            emptyFields.push(f);
                            f.el.dom.value = "";
                        }
                        // M prefer: rendered проверяется выше
                        if(f.isComposite){
                        // if(f.isComposite && f.rendered){
                            f.items.each(setupEmptyFields);
                        }
                    };

                fields.each(setupEmptyFields);
            }
            Ext.Ajax.request(Ext.apply(this.createCallback(o), {
                form:this.form.el.dom,
                url:this.getUrl(isGet),
                method: method,
                headers: o.headers,
                params:!isGet ? this.getParams() : null,
                isUpload: this.form.fileUpload
            }));
            if (o.submitEmptyText === false) {
                Ext.each(emptyFields, function(f) {
                    if (f.applyEmptyText) {
                        f.applyEmptyText();
                    }
                });
            }
        }else if (o.clientValidation !== false){ // client validation failed
            this.failureType = Ext.form.Action.CLIENT_INVALID;
            this.form.afterAction(this, false);
        }
    }
});

/**
 * Метод  вызывается и при клике и при событии change - сюда добавлена обработка
 * атрибута readOnly, тк в стандартном поведении браузеры обрабаытвают этот атрибут только
 * у текстоввых полей
 */
Ext.override(Ext.form.Checkbox, {
    onClick: function (e) {
        if (this.readOnly) {
            e.stopEvent();
            return false;
        }

        if (this.el.dom.checked != this.checked) {
            this.setValue(this.el.dom.checked);
        }
    }
});

/**
 * Раньше нельзя было перейти на конкретную страницу в движках webkit. Т.к.
 * Событие PagingBlur наступает раньше pagingChange, и обновлялась текущая
 * страница, т.к. PagingBlur обновляет индекс.
 */
Ext.override(Ext.PagingToolbar, {
    onPagingBlur: Ext.emptyFn
});

/*
 * Проблема скроллинга хидеров в компонентах ExtPanel или ExtFieldSet
 * (Скролятся только хидеры)
 */

if (Ext.isIE7 || Ext.isIE6) {
    Ext.Panel.override({
        setAutoScroll: function () {
            if (this.rendered && this.autoScroll) {
                var el = this.body || this.el;
                if (el) {
                    el.setOverflow('auto');
                    // Following line required to fix autoScroll
                    el.dom.style.position = 'relative';
                }
            }
        }
    });
}

/**
 * добавим поддержку чекбоксов по аналогии с TreePanel
 * чек боксы включаются просто передачей checked в сторе
 */
Ext.override(Ext.ux.tree.TreeGridNodeUI, {
    renderElements: function (n, a, targetNode, bulkRender) {
        var t = n.getOwnerTree(),
            cb = Ext.isBoolean(a.checked),
            cols = t.columns,
            c = cols[0],
            i, buf, len;

        this.indentMarkup = n.parentNode ? n.parentNode.ui.getChildIndent() : '';

        buf = [
            '<tbody class="x-tree-node">',
            '<tr ext:tree-node-id="', n.id , '" class="x-tree-node-el x-tree-node-leaf x-unselectable ', a.cls, '">',
            '<td class="x-treegrid-col">',
            '<span class="x-tree-node-indent">', this.indentMarkup, "</span>",
            '<img src="', this.emptyIcon, '" class="x-tree-ec-icon x-tree-elbow" />',
            '<img src="', a.icon || this.emptyIcon, '" class="x-tree-node-icon', (a.icon ? " x-tree-node-inline-icon" : ""), (a.iconCls ? " " + a.iconCls : ""), '" unselectable="on" />',
            cb ? ('<input class="x-tree-node-cb" type="checkbox" ' + (a.checked ? 'checked="checked" />' : '/>')) : '',
            '<a hidefocus="on" class="x-tree-node-anchor" href="', a.href ? a.href : '#', '" tabIndex="1" ',
            a.hrefTarget ? ' target="' + a.hrefTarget + '"' : '', '>',
            '<span unselectable="on">', (c.tpl ? c.tpl.apply(a) : a[c.dataIndex] || c.text), '</span></a>',
            '</td>'
        ];

        for (i = 1, len = cols.length; i < len; i++) {
            c = cols[i];
            buf.push(
                '<td class="x-treegrid-col ', (c.cls ? c.cls : ''), '">',
                '<div unselectable="on" class="x-treegrid-text"', (c.align ? ' style="text-align: ' + c.align + ';"' : ''), '>',
                (c.tpl ? c.tpl.apply(a) : a[c.dataIndex]),
                '</div>',
                '</td>'
            );
        }

        buf.push(
            '</tr><tr class="x-tree-node-ct"><td colspan="', cols.length, '">',
            '<table class="x-treegrid-node-ct-table" cellpadding="0" cellspacing="0" style="table-layout: fixed; display: none; width: ', t.innerCt.getWidth(), 'px;"><colgroup>'
        );
        for (i = 0, len = cols.length; i < len; i++) {
            buf.push('<col style="width: ', (cols[i].hidden ? 0 : cols[i].width), 'px;" />');
        }
        buf.push('</colgroup></table></td></tr></tbody>');

        if (bulkRender !== true && n.nextSibling && n.nextSibling.ui.getEl()) {
            this.wrap = Ext.DomHelper.insertHtml("beforeBegin", n.nextSibling.ui.getEl(), buf.join(''));
        } else {
            this.wrap = Ext.DomHelper.insertHtml("beforeEnd", targetNode, buf.join(''));
        }

        this.elNode = this.wrap.childNodes[0];
        this.ctNode = this.wrap.childNodes[1].firstChild.firstChild;
        var cs = this.elNode.firstChild.childNodes;
        this.indentNode = cs[0];
        this.ecNode = cs[1];
        this.iconNode = cs[2];
        var index = 3;
        if (cb) {
            this.checkbox = cs[3];
            // fix for IE6
            this.checkbox.defaultChecked = this.checkbox.checked;
            index++;
        }
        this.anchor = cs[index];
        this.textNode = cs[index].firstChild;
    }
});

/**
 * добавим поддержку чекбоксов по аналогии с TreePanel
 * чек боксы включаются просто передачей checked в сторе
 */
Ext.override(Ext.ux.tree.TreeGrid, {

    /**
     * Retrieve an array of checked nodes, or an array of a specific attribute of checked nodes (e.g. 'id')
     * @param {String} a (optional) Defaults to null (return the actual nodes)
     * @param {TreeNode} startNode (optional) The node to start from, defaults to the root
     * @return {Array}
     */
    getChecked: function (a, startNode) {
        startNode = startNode || this.root;
        var r = [];
        var f = function () {
            if (this.attributes.checked) {
                r.push(!a ? this : (a == 'id' ? this.id : this.attributes[a]));
            }
        };
        startNode.cascade(f);
        return r;
    }
});

/**
 * По-умолчанию ExtJS отправляет за картинкой на 'http://www.extjs.com/s.gif'
 * Тут укажем что они не правы
 */
Ext.apply(Ext, function () {
    return {
        BLANK_IMAGE_URL: Ext.isIE6 || Ext.isIE7 || Ext.isAir ?
            '/m3static/vendor/extjs/resources/images/default/s.gif' :
            'data:image/gif;base64,R0lGODlhAQABAID/AMDAwAAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=='
    };
}());


/**
 * Исправление поведения Ext.ComboBox, когда значения списка с value '' и 0
 * считаются идентичными: теперь сравнение происходит с приведением к строке.
 * Описание ошибки и патч отсюда: http://www.sencha.com/forum/showthread.php?79285
 */
Ext.override(Ext.form.ComboBox, {
    findRecord: function (prop, value) {
        var record;
        if (this.store.getCount() > 0) {
            this.store.each(function (r) {
                if (String(r.data[prop]) == String(value)) {
                    record = r;
                    return false;
                }
            });
        }
        return record;
    }
});

/**
 * Добавление/удаление пользовательского класса m3-grey-field после использования
 * setReadOnly для Ext.form.Field и Ext.form.TriggerField
 * см m3.css - стр. 137 .m3-grey-field
 */
var setReadOnlyField = Ext.form.Field.prototype.setReadOnly.bind({});
var restoreClass = function (readOnly) {
    if (readOnly) {
        this.addClass('m3-grey-field');
        this.el.dom.setAttribute('readonly', '');
    } else {
        this.removeClass('m3-grey-field');
        this.el.dom.removeAttribute('readonly');
    }
};

Ext.override(Ext.form.Field, {
    setReadOnly: function (readOnly) {
        setReadOnlyField.call(this, readOnly);
        restoreClass.call(this, readOnly);
    }
});

var setReadOnlyTriggerField = Ext.form.TriggerField.prototype.setReadOnly;
Ext.override(Ext.form.TriggerField, {
    setReadOnly: function (readOnly) {
        setReadOnlyTriggerField.call(this, readOnly);
        restoreClass.call(this, readOnly);
    }
});

/**
 * #77796 Фикс для корректировки последней колонки в гриде
 * Было:
 *     colModel.setColumnWidth(i, Math.max(grid.minColumnWidth, Math.floor(colWidth + colWidth * fraction)), true);
 * стало:
 *     colModel.setColumnWidth(i, Math.floor(colWidth + colWidth * fraction), true);

 *  Добавлено проставление стилей заголовкам колонок грида
 */
Ext.override(Ext.grid.GridView, {
    renderHeaders : function() {
        var colModel   = this.cm,
            templates  = this.templates,
            headerTpl  = templates.hcell,
            properties = {},
            colCount   = colModel.getColumnCount(),
            last       = colCount - 1,
            cells      = [],
            i, cssCls;

        for (i = 0; i < colCount; i++) {
            if (i == 0) {
                cssCls = 'x-grid3-cell-first ';
            } else {
                cssCls = i == last ? 'x-grid3-cell-last ' : '';
            }

            if(colModel.config[i].cls) {
                cssCls = cssCls + colModel.columns[i].cls;
            }

            properties = {
                id     : colModel.getColumnId(i),
                value  : colModel.getColumnHeader(i) || '',
                style  : this.getColumnStyle(i, true),
                css    : cssCls,
                tooltip: this.getColumnTooltip(i)
            };

            if (colModel.config[i].align == 'right') {
                properties.istyle = 'padding-right: 16px;';
            } else {
                delete properties.istyle;
            }

            cells[i] = headerTpl.apply(properties);
        }

        return templates.header.apply({
            cells : cells.join(""),
            tstyle: String.format("width: {0};", this.getTotalWidth())
        });
    },

    fitColumns: function (preventRefresh, onlyExpand, omitColumn) {
        var grid = this.grid,
            colModel = this.cm,
            totalColWidth = colModel.getTotalWidth(false),
            gridWidth = this.getGridInnerWidth(),
            extraWidth = gridWidth - totalColWidth,
            columns = [],
            extraCol = 0,
            width = 0,
            colWidth, fraction, i;


        if (gridWidth < 20 || extraWidth === 0) {
            return false;
        }

        var visibleColCount = colModel.getColumnCount(true),
            totalColCount = colModel.getColumnCount(false),
            adjCount = visibleColCount - (Ext.isNumber(omitColumn) ? 1 : 0);

        if (adjCount === 0) {
            adjCount = 1;
            omitColumn = undefined;
        }


        for (i = 0; i < totalColCount; i++) {
            if (!colModel.isFixed(i) && i !== omitColumn) {
                colWidth = colModel.getColumnWidth(i);
                columns.push(i, colWidth);

                if (!colModel.isHidden(i)) {
                    extraCol = i;
                    width += colWidth;
                }
            }
        }

        fraction = (gridWidth - colModel.getTotalWidth()) / width;

        while (columns.length) {
            colWidth = columns.pop();
            i = columns.pop();

            colModel.setColumnWidth(i, Math.floor(colWidth + colWidth * fraction), true);
        }


        totalColWidth = colModel.getTotalWidth(false);

        if (totalColWidth > gridWidth) {
            var adjustCol = (adjCount == visibleColCount) ? extraCol : omitColumn,
                newWidth = Math.max(1, colModel.getColumnWidth(adjustCol) - (totalColWidth - gridWidth));

            colModel.setColumnWidth(adjustCol, newWidth, true);
        }

        if (preventRefresh !== true) {
            this.updateAllColumnWidths();
        }

        return true;
    }
});

/**
* В ExtJS по какой-то причине используется parseInt что приводит к ошибкам
* при обработке дробных значений
* было size = side && parseInt(this.getStyle(styles[side]), 10)
* стало size = side && parseFloat(this.getStyle(styles[side]))
*/
Ext.override(Ext.Element, {
    addStyles : function(sides, styles){
        var ttlSize = 0,
            sidesArr = sides.match(/\w/g),
            side,
            size,
            i,
            len = sidesArr.length;
        for (i = 0; i < len; i++) {
            side = sidesArr[i];
            size = side && parseFloat(this.getStyle(styles[side]));
            if (size) {
                ttlSize += Math.abs(size);
            }
        }
        return ttlSize;
    }
});

/**
* Предотвращает баг при котором показывается множество тултипов из-за
* задержки показывания при проведении мышкой по нескольким элементам по очереди
* например в гриде
*/
Ext.ToolTip.override({
    onMouseMove : function(e){
        var t = this.delegate ? e.getTarget(this.delegate) : this.triggerElement = true;
        if (t) {
            this.targetXY = e.getXY();
            if (t === this.triggerElement) {
                if(!this.hidden && this.trackMouse){
                    this.setPagePosition(this.getTargetXY());
                }
            } else {
                this.hide();
                this.lastActive = new Date(0);
                this.onTargetOver(e);
            }
        } else if (!this.closable) {
            this.clearTimer('show');
            this.hide();
        }
    }
});

/**
* Унифицирует поведение в современных браузерах.
* В старых версиях firefox на платформах отличных от Windows, была нужна
* обработка события keypress. Сейчас нормально работает keydown.
*/
Ext.apply(Ext.EventManager, {
        useKeydown: Ext.isWebKit ?
            Ext.num(navigator.userAgent.match(/AppleWebKit\/(\d+)/)[1]) >= 525 :
            !Ext.isOpera
});

/**
 * Добавление возможности указать флаг exactMatch для поиска точного совпадения.
 */
Ext.data.Store.override({
    find: function(property, value, start, anyMatch, caseSensitive, exactMatch) {
        var fn = this.createFilterFn(property, value, anyMatch, caseSensitive, exactMatch);
        return fn ? this.data.findIndexBy(fn, null, start) : -1;
    }
});

/**
 * Исправляет отрисовку таблиц с многоуровневыми заголовками в браузерах на WebKit.
 */
Ext.ux.grid.ColumnHeaderGroup.override({

    getGroupStyle: function(group, gcol) {
        var width = 0, hidden = true;
        for (var i = gcol, len = gcol + group.colspan; i < len; i++) {
            if (!this.cm.isHidden(i)) {
                var cw = this.cm.getColumnWidth(i);
                if (typeof cw == 'number') {
                    width += cw;
                }
                hidden = false;
            }
        }
        return {
            width: (Ext.isBorderBox ? width : Math.max(width - this.borderWidth, 0)) + 'px',
            hidden: hidden
        };
    }
});

/**
 * Предотвращает исчезновение названий шрифта из селекта при переключении из-за проверки
 */
Ext.override(Ext.form.HtmlEditor, {
    updateToolbar: function(){

        if(this.readOnly){
            return;
        }

        if(!this.activated){
            this.onFirstFocus();
            return;
        }

        var btns = this.tb.items.map,
            doc = this.getDoc();

        if(this.enableFont && !Ext.isSafari2){
            var name = (doc.queryCommandValue('FontName').split(', ')[0].replace(/"/g, '')||this.defaultFont).toLowerCase();
            if(name != this.fontSelect.dom.value){
                this.fontSelect.dom.value = name;
            }
        }
        if(this.enableFormat){
            btns.bold.toggle(doc.queryCommandState('bold'));
            btns.italic.toggle(doc.queryCommandState('italic'));
            btns.underline.toggle(doc.queryCommandState('underline'));
        }
        if(this.enableAlignments){
            btns.justifyleft.toggle(doc.queryCommandState('justifyleft'));
            btns.justifycenter.toggle(doc.queryCommandState('justifycenter'));
            btns.justifyright.toggle(doc.queryCommandState('justifyright'));
        }
        if(!Ext.isSafari2 && this.enableLists){
            btns.insertorderedlist.toggle(doc.queryCommandState('insertorderedlist'));
            btns.insertunorderedlist.toggle(doc.queryCommandState('insertunorderedlist'));
        }

        Ext.menu.MenuMgr.hideAll();

        this.syncValue();
    }
});