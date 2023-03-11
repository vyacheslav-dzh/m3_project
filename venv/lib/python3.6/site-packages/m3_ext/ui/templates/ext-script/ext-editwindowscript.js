(function (){
    var win = {{ window.render|safe }};
    var forceClose = {{ window.force_close|yesno:'true,false' }};
	function submitForm(btn, e, baseParams) { win.submitForm(btn, e, baseParams); }
	function cancelForm(){ win.close(forceClose); }
    
	{{ window.render_globals }}

    win.show();    
    return win;
})()