"""Ledger defaults and explicit per-document overrides; no Google writes in this dialog."""
import tkinter as tk
from tkinter import ttk,messagebox
from sheet_connections import FAMILIES,SCOPES,default_connection,normalized,chosen,locked,spreadsheet_id


def open_defaults(app,parent=None):
    parent=parent or app.root;win=tk.Toplevel(parent);win.title('대장별 구글 시트 연결')
    win.geometry(f"960x{min(760,win.winfo_screenheight()-100)}");win.minsize(760,480);win.transient(parent);win.grab_set()
    footer=ttk.Frame(win,padding=12);footer.pack(side='bottom',fill='x')
    ttk.Label(win,text='각 대장에 현재 적용 중인 스프레드시트 주소와 하단 탭 이름을 표시합니다. 표시된 값을 바로 수정해 저장하세요.\n주소나 탭을 비우면 해당 대장의 기본 설정을 사용합니다. 기존 등록 건은 당시 연결을 유지하며 새로 등록할 문서부터 변경한 기본 연결을 사용합니다.',wraplength=880,padding=14).pack(fill='x')
    from modern_ui import ScrollablePanel
    panel=ScrollablePanel(win);panel.pack(fill='both',expand=True);body=panel.body;body.columnconfigure(1,weight=1)
    fields={};original={};initial={};extras={};win.connection_fields=fields;win.input_settings=extras
    def draft_entries():
        result={}
        for key,values in fields.items():
            item={}
            for name,var in values.items():
                text=var.get().strip()
                if text==initial[key][name]:text=str(original[key].get(name,'')).strip()
                if text:item[name]=text
            item.update(extras[key])
            if item:result[key]=item
        return result
    for index,(scope,family) in enumerate((s,f) for f in FAMILIES for s in SCOPES):
        key=scope+':'+family;old=app.config.get('ledger_connections',{}).get(key,{})
        original[key]=old.copy();extras[key]={k:old[k] for k in ('layout','defaults') if k in old};target=default_connection(app.config,scope,family)
        address=target['spreadsheet_id']
        if address:
            try:address='https://docs.google.com/spreadsheets/d/'+spreadsheet_id(address)+'/edit'
            except ValueError:pass  # Let the user correct an invalid saved value.
        initial[key]={'spreadsheet_id':address,'tab':target['tab']}
        fields[key]={name:tk.StringVar(value=value) for name,value in initial[key].items()}
        ttk.Label(body,text=family+' · '+SCOPES[scope]).grid(row=index*2,column=0,sticky='w',padx=10,pady=(12,4))
        ttk.Entry(body,textvariable=fields[key]['spreadsheet_id']).grid(row=index*2,column=1,sticky='ew',padx=10,pady=(12,4))
        ttk.Label(body,text='하단 탭 이름').grid(row=index*2+1,column=0,sticky='w',padx=10,pady=(0,10))
        ttk.Entry(body,textvariable=fields[key]['tab']).grid(row=index*2+1,column=1,sticky='ew',padx=10,pady=(0,10))
        def test(scope=scope,family=family,key=key):
            target=default_connection({**app.config,'ledger_connections':draft_entries()},scope,family)
            try:target=normalized(target)
            except ValueError as error:return messagebox.showerror('연결 확인',str(error),parent=win)
            if app.send('test_sheet_target',{'scope':scope,'family':family,'target':target}):close()
        ttk.Button(body,text='읽기 확인',command=test).grid(row=index*2,column=2,padx=8)
        def edit(scope=scope,family=family,key=key):
            from ledger_layout_ui import open_editor
            def apply(layout,defaults):
                extras[key]={'layout':layout,**({'defaults':defaults} if defaults else {})}
            open_editor(app,win,scope,family,extras[key].get('layout'),extras[key].get('defaults'),apply)
        ttk.Button(body,text='열 · 기본 입력값',command=edit).grid(row=index*2+1,column=2,padx=8)

    def close():
        win.destroy()
        if isinstance(parent,tk.Toplevel) and parent.winfo_exists():parent.grab_set()
    def save():
        result={}
        try:
            for key,item in draft_entries().items():
                if item.get('spreadsheet_id'):item['spreadsheet_id']=spreadsheet_id(item['spreadsheet_id'])
                if item.get('tab') and any(c in item['tab'] for c in '\r\n\t'):raise ValueError('탭 이름은 한 줄로 입력하세요.')
                if item:result[key]=item
        except ValueError as error:return messagebox.showerror('연결 설정',str(error),parent=win)
        app.config['ledger_connections']=result;app.persist_config();app.commands.put(('config',app.config.copy()));close()
        app.status.set('대장별 기본 연결을 저장했습니다. 새 문서부터 적용합니다.')
    win.protocol('WM_DELETE_WINDOW',close)
    ttk.Button(footer,text='닫기',command=close).pack(side='left')
    ttk.Button(footer,text='기본 연결 저장',command=save).pack(side='right')
    return win


def open_document(app,key,scope,family,parent=None):
    parent=parent or app.root;job=app.jobs[key];target=chosen(app.config,job,scope,family)
    win=tk.Toplevel(parent);win.title('이 문서의 '+SCOPES[scope]+' 연결');win.transient(parent);win.grab_set()
    win.geometry('820x370');box=ttk.Frame(win,padding=18);box.pack(fill='both',expand=True);box.columnconfigure(1,weight=1)
    values={k:tk.StringVar(value=target.get(k,'')) for k in ('spreadsheet_id','tab')};win.connection_fields=values
    layout=target.get('layout');fixed=locked(job,scope)
    for row,(field,label) in enumerate((('spreadsheet_id','스프레드시트 주소 또는 ID'),('tab','하단 탭 이름'))):
        ttk.Label(box,text=label).grid(row=row,column=0,sticky='w',pady=10)
        ttk.Entry(box,textvariable=values[field],state='readonly' if fixed else 'normal').grid(row=row,column=1,sticky='ew',padx=12)
    ttk.Label(box,text='이 대장은 등록을 시작해 당시 연결을 유지합니다.' if fixed else '이 문서에만 적용합니다. 등록을 시작한 뒤에는 해당 연결을 유지합니다.',wraplength=740).grid(row=2,column=0,columnspan=2,sticky='w',pady=12)
    actions=ttk.Frame(box);actions.grid(row=3,column=0,columnspan=2,sticky='ew',pady=12)
    def close():win.destroy()
    def save(reset=False):
        try:target=None if reset else normalized({**{k:v.get() for k,v in values.items()},**({'layout':layout} if layout else {})})
        except ValueError as error:return messagebox.showerror('연결 설정',str(error),parent=win)
        if app.send('sheet_override',{'hash':key,'scope':scope,'target':target}):close()
    def test():
        try:target=normalized({**{k:v.get() for k,v in values.items()},**({'layout':layout} if layout else {})})
        except ValueError as error:return messagebox.showerror('연결 확인',str(error),parent=win)
        if app.send('test_sheet_target',{'scope':scope,'family':family,'target':target}):close()
    def edit():
        from ledger_layout_ui import open_editor
        def apply(result,_):
            nonlocal layout
            layout=result
        open_editor(app,win,scope,family,layout,{},apply,readonly=fixed,show_defaults=False)
    ttk.Button(box,text='이 문서의 열 설정 확인 · 변경',command=edit).grid(row=4,column=0,columnspan=2,sticky='w',pady=6)
    ttk.Button(actions,text='닫기',command=close).pack(side='left')
    ttk.Button(actions,text='읽기 확인',command=test).pack(side='left',padx=8)
    ttk.Button(actions,text='기본 연결 사용',command=lambda:save(True),state='disabled' if fixed else 'normal').pack(side='right')
    ttk.Button(actions,text='이 문서에 적용',command=save,state='disabled' if fixed else 'normal').pack(side='right',padx=8)
    return win
