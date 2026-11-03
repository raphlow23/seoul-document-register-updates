"""Add a private form with explicit, reviewable locations for automatic metadata."""
from pathlib import Path
import hashlib
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import template_library as library


def open_pick_template(app,parent=None,on_added=None):
    parent=parent or app.root
    path=filedialog.askopenfilename(parent=parent,title="양식으로 사용할 HWP 파일",filetypes=[("한글 양식","*.hwp")])
    if path:return open_add_template(app,parent=parent,on_added=on_added,source=path)


def open_add_template(app,parent=None,on_added=None,source=None):
    parent=parent or app.root
    win=tk.Toplevel(parent);win.title("HWP 양식 추가")
    win.geometry(f"980x{min(760,win.winfo_screenheight()-100)}");win.transient(parent);win.grab_set()
    outer=ttk.Frame(win,padding=16);outer.pack(fill="both",expand=True)
    actions=ttk.Frame(outer);actions.pack(side="bottom",fill="x",pady=(12,0))
    tabs=ttk.Notebook(outer);tabs.pack(fill="both",expand=True)
    basic=ttk.Frame(tabs,padding=12);mapping=ttk.Frame(tabs,padding=12)
    tabs.add(basic,text="1. 양식과 저장 위치");tabs.add(mapping,text="2. 번호·날짜 위치 확인")
    values={key:tk.StringVar(value="공문" if key=="family" else "") for key in ("source","label","family","folder","prefix","suffix","default_receiver",*library.TOKENS)}
    source_text=[""];source_hash=[""];position_boxes={}
    preview=tk.Text(mapping,height=9,wrap="word",font=("맑은 고딕",10),exportselection=False)
    preview.pack(fill="both",expand=True,pady=(0,8));preview.configure(state="disabled")
    labels={"number":"시행번호 (필수)","date":"시행일 (필수)","title":"제목 (선택)","receiver":"수신처 (선택)","sender":"담당자 (선택)"}
    locations=ttk.Frame(mapping);locations.pack(fill="x")
    selected=tk.StringVar(value="number")
    for i,(key,label) in enumerate(labels.items()):
        ttk.Radiobutton(locations,text=label,variable=selected,value=key).grid(row=i,column=0,sticky="w",pady=3)
        ttk.Entry(locations,textvariable=values[key]).grid(row=i,column=1,sticky="ew",padx=10)
        box=ttk.Combobox(locations,state="readonly",width=35);box.grid(row=i,column=2,sticky="ew");position_boxes[key]=box
        def changed(*_,field=key):
            from hwp_anchor_slots import occurrences
            matches=occurrences(source_text[0],values[field].get().strip())
            target=position_boxes[field]
            target.configure(values=[f"{n+1}번째: {context[:70]}" for n,(_,context) in enumerate(matches)])
            if len(matches)>1:target.set("같은 문구 중 입력 위치 선택");target.grid()
            else:target.set("");target.grid_remove()
        values[key].trace_add('write',changed);box.grid_remove()
    locations.columnconfigure(1,weight=1)
    def use_selection():
        try:text=preview.get("sel.first","sel.last").strip()
        except tk.TclError:return messagebox.showinfo("문구 선택","위 문서에서 번호 또는 날짜의 현재 문구를 먼저 선택하세요.",parent=win)
        values[selected.get()].set(text)
    ttk.Button(mapping,text="위에서 선택한 문구를 선택한 위치로 지정",command=use_selection).pack(anchor="w",pady=6)
    ttk.Label(mapping,text="번호·날짜를 자동 제안합니다. 같은 문구가 여러 곳이면 오른쪽에서 바꿀 위치를 고르세요. 본문의 다른 기관 번호·날짜는 그대로 둡니다.\n번호·날짜 자리가 비어 있으면 원본 한글에 {{발신번호}}, {{발신일}}을 넣어 주세요. 제목·수신처·담당자는 지정한 경우에 자동 입력합니다.",wraplength=900).pack(anchor="w")
    def default_folder():
        family=values["family"].get();relative={"공문":"0.시행문(센터)","업무연락":"0.시행문(업무연락)","처리지침":"0.시행문(처리지침)"}[family]
        values["folder"].set(str(Path(app.config.get("root",""))/relative))
        values["prefix"].set("처리지침" if family=="처리지침" else "")
    def browse(path=None):
        path=path or filedialog.askopenfilename(parent=win,title="추가할 원본 HWP 양식",filetypes=[("한글 양식","*.hwp")])
        if not path:return
        try:
            text=library.inspect(path);source_hash[0]=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception as error:return messagebox.showerror("양식 확인",str(error),parent=win)
        values["source"].set(path);values["label"].set(Path(path).stem)
        values['family'].set('처리지침' if '처리지침' in Path(path).stem else '업무연락' if '업무연락' in Path(path).stem else '공문');default_folder()
        source_text[0]=text
        preview.configure(state="normal");preview.delete("1.0","end");preview.insert("1.0",text);preview.configure(state="disabled")
        for key,value in library.suggestions(text).items():values[key].set(value)
        hints=library.filename_hints(path)
        for key in ("prefix","suffix"):
            if key in hints:values[key].set(hints[key])
        if "spacing" in hints:spacing.set(hints["spacing"])
    for i,(key,label) in enumerate((("source","원본 HWP"),("label","양식 이름"),("family","공문 종류"),("folder","기본 저장 폴더"),("default_receiver","기본 수신처 (선택)"),("prefix","파일명 번호 앞 표시"),("suffix","파일명 제목 뒤 표시"))):
        ttk.Label(basic,text=label).grid(row=i,column=0,sticky="w",pady=10)
        widget=ttk.Combobox(basic,textvariable=values[key],values=("공문","업무연락","처리지침"),state="readonly") if key=="family" else ttk.Entry(basic,textvariable=values[key])
        widget.grid(row=i,column=1,sticky="ew",padx=10)
        if key=="family":widget.bind("<<ComboboxSelected>>",lambda e:default_folder())
        if key=="source":ttk.Button(basic,text="파일 선택",command=browse).grid(row=i,column=2)
        if key=="folder":
            def folder():
                path=filedialog.askdirectory(parent=win)
                if path:values["folder"].set(path)
            ttk.Button(basic,text="폴더 선택",command=folder).grid(row=i,column=2)
    spacing=tk.BooleanVar(value=True)
    ttk.Checkbutton(basic,text="파일명 번호 뒤 마침표 다음에 공백 사용",variable=spacing).grid(row=7,column=0,columnspan=3,sticky="w",pady=8)
    ttk.Label(basic,text="파일명 예: 012. 26.11.02. 선택한 제목.hwp\n문서 하단: 서울센터 2026-012 / (26. 11. 2)\n양식은 이 PC에 복사해 보관하며 업데이트 후에도 유지됩니다.",wraplength=850).grid(row=8,column=0,columnspan=3,sticky="w",pady=12)
    basic.columnconfigure(1,weight=1);default_folder()
    drop=ttk.Label(basic,text="HWP 양식 파일을 여기로 끌어다 놓아도 됩니다.",padding=12,relief='groove',anchor='center')
    drop.grid(row=9,column=0,columnspan=3,sticky='ew',pady=6)
    try:has_drop=bool(win.tk.call('package','present','tkdnd'))
    except tk.TclError:has_drop=False
    if has_drop:
        from tkinterdnd2 import DND_FILES,COPY,REFUSE_DROP
        def dropped(event):
            paths=win.tk.splitlist(event.data)
            if len(paths)!=1 or Path(paths[0]).suffix.lower()!='.hwp':
                messagebox.showinfo('HWP 양식','한 번에 HWP 양식 파일 하나를 넣어 주세요.',parent=win);return REFUSE_DROP
            browse(paths[0]);return COPY
        for target in (win,drop,preview):target.drop_target_register(DND_FILES);target.dnd_bind('<<Drop>>',dropped)
    def close():
        win.destroy()
        if isinstance(parent,tk.Toplevel) and parent.winfo_exists():parent.grab_set()
    def save():
        settings={key:value.get().strip() for key,value in values.items()};settings["spacing"]=spacing.get()
        settings['_occurrences']={key:box.current() for key,box in position_boxes.items() if len(box.cget('values'))>1}
        settings['_source_sha256']=source_hash[0]
        try:item=library.add(settings.pop("source"),settings,storage=app.data/"hwp-templates")
        except Exception as error:
            tabs.select(mapping)
            return messagebox.showerror("양식 추가 확인",str(error),parent=win)
        close();app.status.set("양식을 추가했습니다: "+item["label"])
        if on_added:on_added(item)
    win.protocol("WM_DELETE_WINDOW",close)
    ttk.Button(actions,text="취소",command=close).pack(side="left")
    ttk.Button(actions,text="양식 추가",command=save).pack(side="right")
    if source:browse(source);tabs.select(mapping)
    return win
