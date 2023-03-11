{# Функция разворачивает узел, если имеются загруженные дочерние узлы #}
function expandChildren(node) {
    node.eachChild(function (child) {
        if (child.attributes.children) {
            child.expand();
            expandChildren(child);
        }
    })
}
{# Функция для установки хэндлера при загрузке данных грида #}
function onTreeGridLoad() {
    var grid = Ext.getCmp('{{ component.grid.client_id}}');
    grid.getLoader().on('load', function (treeLoader, node, response) {
        expandChildren(node)
    });
}

onTreeGridLoad();