"""Add a private form with explicit, reviewable locations for automatic metadata."""
from pathlib import Path
import hashlib
import os
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import template_library as library


def open_pick_template(app,parent=None,on_added=None,replace_id=None):
    parent=parent or app.root
    path=filedialog.askopenfilename(parent=parent,title="양식으로 사용할 HWP 파일",filetypes=[("한글 양식","*.hwp")])
    if path:return open_add_template(app,parent=parent,on_added=on_added,source=path,replace_id=replace_id)


def open_edit_template(app,key,parent=None,on_saved=None):
    import hwp_documents as docs
    item=docs.style(key)
    if not (docs.template_root()/item['file']).is_file():
        return open_pick_template(app,parent=parent,on_added=on_saved,replace_id=key)
    try:source=library.edit_copy(key,app.config)
    except (OSError,ValueError) as error:return messagebox.showerror('양식 수정',str(error),parent=parent or app.root)
    return open_add_template(app,parent=parent,on_added=on_saved,source=source,replace_id=key,editable=True)


def open_add_template(app,parent=None,on_added=None,source=None,replace_id=None,editable=False):
    parent=parent or app.root
    win=tk.Toplevel(parent);win.title('HWP 양식 수정' if editable else "원본 HWP 연결 · 자동 입력 위치" if replace_id else "HWP 양식 추가")
    win.geometry(f"{min(980,win.winfo_screenwidth()-60)}x{min(760,win.winfo_screenheight()-100)}");win.transient(parent);win.grab_set()
    outer=ttk.Frame(win,padding=16);outer.pack(fill="both",expand=True)
    actions=ttk.Frame(outer);actions.pack(side="bottom",fill="x",pady=(12,0))
    tabs=ttk.Notebook(outer);tabs.pack(fill="both",expand=True)
    from modern_ui import ScrollablePanel
    basic_page=ScrollablePanel(tabs);mapping_page=ScrollablePanel(tabs)
    basic=ttk.Frame(basic_page.body,padding=8);basic.pack(fill='x')
    mapping=ttk.Frame(mapping_page.body,padding=8);mapping.pack(fill='x')
    tabs.add(basic_page,text="1. 양식 파일·저장 위치");tabs.add(mapping_page,text="2. 자동 입력 위치")
    values={key:tk.StringVar(value="공문" if key=="family" else "") for key in ("source","label","family","folder","prefix","suffix","default_receiver",*library.TOKENS)}
    win.template_values=values
    source_text=[""];source_hash=[""];source_path=[""];position_boxes={}
    ttk.Label(mapping,text="시행번호와 시행일이 공문 하단을 가리키는지 확인하세요. 바꾸려면 원문에서 문구를 드래그한 뒤 해당 항목의 지정 버튼을 누르세요.",wraplength=700).pack(fill='x',pady=(0,6))
    original=ttk.LabelFrame(mapping,text='원본 HWP 문구')
    original.pack(fill='x',pady=(0,8))
    preview=tk.Text(original,height=7,wrap="word",font=("맑은 고딕",10),exportselection=False)
    scrollbar=ttk.Scrollbar(original,command=preview.yview);preview.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right',fill='y');preview.pack(fill="x",expand=True);preview.configure(state="disabled")
    labels={"number":"시행번호","date":"시행일","title":"제목","receiver":"수신처","sender":"담당자"}
    locations=ttk.Frame(mapping);locations.pack(fill="x")
    def use_selection(key):
        try:
            selected=preview.get("sel.first","sel.last")
            offset=len(preview.get("1.0","sel.first"))+len(selected)-len(selected.lstrip())
        except tk.TclError:selected=''
        if not selected.strip():
            return messagebox.showinfo("원문 문구 선택","위 원본에서 "+labels[key]+"에 해당하는 문구를 드래그하세요.",parent=win)
        values[key].set(selected.strip())
        box=position_boxes[key]
        if len(box.cget('values'))>1:
            from hwp_anchor_slots import occurrences
            for index,(start,_) in enumerate(occurrences(source_text[0],selected.strip())):
                if start==offset:
                    box.current(index);box.event_generate('<<ComboboxSelected>>');break
    for i,(key,label) in enumerate(labels.items()):
        required=key in ('number','date')
        ttk.Label(locations,text=label+(' (필수)' if required else ' (선택)')).grid(row=2*i,column=0,sticky="w",pady=2)
        ttk.Entry(locations,textvariable=values[key]).grid(row=2*i,column=1,sticky="ew",padx=6)
        caption=label+("으로 지정" if key=='title' else "로 지정")
        ttk.Button(locations,text=caption,command=lambda field=key:use_selection(field)).grid(row=2*i,column=2,padx=4)
        status=ttk.Label(locations,text='지정 필요' if required else '미사용',width=10)
        status.grid(row=2*i,column=3,sticky='w',padx=2)
        box=ttk.Combobox(locations,state="readonly",width=24)
        box.grid(row=2*i+1,column=1,columnspan=3,sticky="ew",padx=6,pady=(0,3));position_boxes[key]=box
        def changed(*_,field=key,state=status):
            from hwp_anchor_slots import occurrences
            matches=occurrences(source_text[0],values[field].get().strip())
            target=position_boxes[field]
            target.configure(values=[f"{n+1}번째: {context[:70]}" for n,(_,context) in enumerate(matches)])
            if len(matches)>1:
                target.set("같은 문구 중 바꿀 위치를 선택하세요");target.grid();state.configure(text="위치 선택")
            else:
                target.set("");target.grid_remove()
                if matches:state.configure(text='지정됨')
                elif values[field].get().strip():state.configure(text='원문 확인')
                else:state.configure(text='지정 필요' if field in ('number','date') else '미사용')
        box.bind('<<ComboboxSelected>>',lambda _,state=status:state.configure(text='지정됨'))
        values[key].trace_add('write',changed);box.grid_remove()
    locations.columnconfigure(1,weight=1)
    ttk.Label(mapping,text="원본에 시행번호나 날짜가 비어 있으면 한글에서 {{발신번호}}, {{발신일}}을 넣은 뒤 파일을 다시 읽어 주세요. 제목·수신처·담당자는 지정한 항목만 발신할 때 채웁니다.",wraplength=700).pack(anchor="w",pady=(8,0))
    def default_folder():
        family=values["family"].get();relative={"공문":"0.시행문(센터)","업무연락":"0.시행문(업무연락)","처리지침":"0.시행문(처리지침)"}[family]
        values["folder"].set(str(Path(app.config.get("root",""))/relative))
        values["prefix"].set("처리지침" if family=="처리지침" else "")
    def browse(path=None):
        path=path or filedialog.askopenfilename(parent=win,title="추가할 원본 HWP 양식",filetypes=[("한글 양식","*.hwp")])
        if not path:return
        keep={key:values[key].get() for key in ('label','family','folder','prefix','suffix','default_receiver')} if editable and source_text[0] else None
        keep_spacing=spacing.get()
        try:
            text=library.inspect(path)
            if editable and Path(path).resolve().parent!=Path(source).resolve().parent:path=library.copy_for_edit(path,app.config)
            source_hash[0]=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception as error:
            messagebox.showerror("양식 확인",str(error),parent=win);return False
        source_path[0]=str(path)
        values["source"].set(path);values["label"].set(Path(path).stem)
        values['family'].set('처리지침' if '처리지침' in Path(path).stem else '업무연락' if '업무연락' in Path(path).stem else '공문');default_folder()
        source_text[0]=text
        preview.configure(state="normal");preview.delete("1.0","end");preview.insert("1.0",text);preview.configure(state="disabled")
        for key,value in library.suggestions(text).items():values[key].set(value)
        hints=library.filename_hints(path)
        for key in ("prefix","suffix"):
            if key in hints:values[key].set(hints[key])
        if "spacing" in hints:spacing.set(hints["spacing"])
        if replace_id:
            import hwp_documents as docs
            existing=docs.style(replace_id)
            values['label'].set(existing['label']);values['family'].set(existing['family'])
            values['folder'].set(docs.destination(app.config,existing))
            if editable:
                for key in ('prefix','suffix','default_receiver'):values[key].set(existing.get(key,''))
                spacing.set(bool(existing.get('spacing',' ')))
                anchors={'number':existing.get('old_number',''),'date':existing.get('old_date',''),'receiver':existing.get('receiver',''),'sender':existing.get('old_sender',''),'title':next(iter(existing.get('title_fragments',[])),'')}
                for key,value in anchors.items():
                    if value and value in text:values[key].set(value)
        if keep:
            for key,value in keep.items():values[key].set(value)
            spacing.set(keep_spacing)
        return True
    for i,(key,label) in enumerate((("source","원본 HWP"),("label","양식 이름"),("family","공문 종류"),("folder","기본 저장 폴더"),("default_receiver","기본 수신처 (선택)"),("prefix","파일명 번호 앞 표시"),("suffix","파일명 제목 뒤 표시"))):
        row=i+3 if i else 0
        ttk.Label(basic,text=label).grid(row=row,column=0,sticky="w",pady=5)
        widget=ttk.Combobox(basic,textvariable=values[key],values=("공문",) if replace_id=='8' else ("공문","업무연락","처리지침"),state="readonly") if key=="family" else ttk.Entry(basic,textvariable=values[key])
        widget.grid(row=row,column=1,sticky="ew",padx=10)
        if key=="family":widget.bind("<<ComboboxSelected>>",lambda e:default_folder())
        if key=="source":ttk.Button(basic,text="파일 선택",command=browse).grid(row=row,column=2)
        if key=="folder":
            def folder():
                path=filedialog.askdirectory(parent=win)
                if path:values["folder"].set(path)
            ttk.Button(basic,text="폴더 선택",command=folder).grid(row=row,column=2)
    spacing=tk.BooleanVar(value=True)
    ttk.Checkbutton(basic,text="파일명 번호 뒤 마침표 다음에 공백 사용",variable=spacing).grid(row=10,column=0,columnspan=3,sticky="w",pady=6)
    ttk.Label(basic,text="파일명 예시   012. 26.11.02. 선택한 제목.hwp\n문서 하단 예시   서울센터 2026-012 / (26. 11. 2)",wraplength=700).grid(row=11,column=0,columnspan=3,sticky="w",pady=4)
    basic.columnconfigure(1,weight=1);default_folder()
    drop=ttk.Label(basic,text="HWP 양식 파일을 여기로 끌어다 놓아도 됩니다.",padding=12,relief='groove',anchor='center')
    try:has_drop=bool(win.tk.call('package','present','tkdnd'))
    except tk.TclError:has_drop=False
    if has_drop:
        drop.grid(row=1,column=0,columnspan=3,sticky='ew',pady=4)
        from tkinterdnd2 import DND_FILES,COPY,REFUSE_DROP
        def dropped(event):
            paths=win.tk.splitlist(event.data)
            if len(paths)!=1 or Path(paths[0]).suffix.lower()!='.hwp':
                messagebox.showinfo('HWP 양식','한 번에 HWP 양식 파일 하나를 넣어 주세요.',parent=win);return REFUSE_DROP
            browse(paths[0]);return COPY
        for target in (win,drop,preview):target.drop_target_register(DND_FILES);target.dnd_bind('<<Drop>>',dropped)
    def basic_ready():
        path=values['source'].get().strip()
        if path and path!=source_path[0]:
            if not browse(path):return False
        if not path:error="1단계에서 원본 HWP 파일을 선택하세요."
        elif not values['label'].get().strip():error="1단계에서 양식 이름을 입력하세요."
        elif not Path(values['folder'].get().strip()).is_absolute():error="1단계에서 작성한 공문을 저장할 기본 폴더를 선택하세요."
        else:return True
        messagebox.showinfo("양식 기본 정보",error,parent=win)
        return False
    def close():
        win.destroy()
        if isinstance(parent,tk.Toplevel) and parent.winfo_exists():parent.grab_set()
    def save():
        if not basic_ready():
            tabs.select(basic_page)
            return
        if editable:
            try:changed=hashlib.sha256(Path(values['source'].get()).read_bytes()).hexdigest()!=source_hash[0]
            except OSError as error:return messagebox.showerror('수정본 확인','한글에서 저장하고 닫은 뒤 다시 읽어 주세요.\n'+str(error),parent=win)
            if changed:
                if not browse(values['source'].get()):return
                tabs.select(mapping_page)
                return messagebox.showinfo('수정본 확인','수정한 파일을 다시 읽었습니다. 번호·날짜 입력 위치를 확인한 뒤 수정 내용 저장을 눌러 주세요.',parent=win)
        settings={key:value.get().strip() for key,value in values.items()};settings["spacing"]=spacing.get()
        settings['_edit_existing']=editable
        settings['_occurrences']={key:box.current() for key,box in position_boxes.items() if len(box.cget('values'))>1}
        settings['_source_sha256']=source_hash[0]
        try:item=library.add(settings.pop("source"),settings,storage=app.data/"hwp-templates",replace_id=replace_id)
        except Exception as error:
            tabs.select(mapping_page)
            return messagebox.showerror("양식 추가 확인",str(error),parent=win)
        if editable:
            app.config.setdefault('outgoing_folders',{})[item['id']]=settings['folder']
            app.persist_config();app.commands.put(('config',app.config.copy()))
        close();app.status.set(("양식을 연결했습니다: " if replace_id else "양식을 추가했습니다: ")+item["label"])
        if on_added:on_added(item)
    win.protocol("WM_DELETE_WINDOW",close)
    ttk.Button(actions,text="취소",command=close).pack(side="left")
    step=tk.StringVar()
    ttk.Label(actions,textvariable=step).pack(side='left',padx=12)
    next_button=ttk.Button(actions,text='다음: 자동 입력 위치 확인',style='Accent.TButton',command=lambda:tabs.select(mapping_page))
    previous_button=ttk.Button(actions,text='이전: 양식 기본 정보',command=lambda:tabs.select(basic_page))
    save_button=ttk.Button(actions,text='수정 내용 저장' if editable else "HWP 연결 저장" if replace_id else "양식 추가",style='Accent.TButton',command=save)
    def show_actions(_=None):
        editing_positions=tabs.select()==str(mapping_page)
        if editing_positions and not basic_ready():
            tabs.select(basic_page)
            return
        for button in (next_button,previous_button,save_button):button.pack_forget()
        if editing_positions:
            step.set('2 / 2 · 자동 입력 위치')
            save_button.pack(side='right')
            previous_button.pack(side='right',padx=6)
        else:
            step.set('1 / 2 · 양식 기본 정보')
            next_button.pack(side='right')
    tabs.bind('<<NotebookTabChanged>>',show_actions)
    if editable:
        def open_native():
            try:
                path=values['source'].get()
                if Path(path).resolve().parent!=Path(source).resolve().parent:
                    if not browse(path):return
                    path=values['source'].get()
                os.startfile(path)
            except OSError as error:messagebox.showerror('한글 열기',str(error),parent=win)
        edit_actions=ttk.Frame(basic);edit_actions.grid(row=2,column=0,columnspan=3,sticky='w',pady=4)
        ttk.Button(edit_actions,text='한글에서 양식 수정',command=open_native).pack(side='left')
        ttk.Button(edit_actions,text='수정본 다시 읽기',command=lambda:browse(values['source'].get())).pack(side='left',padx=6)
        ttk.Label(basic,text='한글에서 Ctrl+S로 저장하고 닫은 뒤 수정본을 다시 읽어 주세요.',wraplength=700).grid(row=3,column=0,columnspan=3,sticky='w',pady=(0,6))
    if source:browse(source)
    show_actions()
    return win
