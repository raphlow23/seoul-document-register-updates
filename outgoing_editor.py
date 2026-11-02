"""Outgoing registration, stamp metadata and editable text drafting in one dialog."""
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import uuid

from outgoing import defaults,METHODS
from document_draft import PURPOSES,build_body,full_text,reflow
import hwp_documents as hwp_docs

FAMILY_LABELS={"시행문(공문)":"공문","업무연락 발신":"업무연락","처리지침 발신":"처리지침"}


def folder_settings_dialog(app,parent,on_save=None):
    win=tk.Toplevel(parent);win.title("발신 양식별 기본 저장 위치");win.transient(parent);win.grab_set()
    box=ttk.Frame(win,padding=14);box.pack(fill="both",expand=True)
    fields={}
    for row,item in enumerate(hwp_docs.catalog()):
        fields[item["id"]]=tk.StringVar(value=hwp_docs.destination(app.config,item))
        ttk.Label(box,text=item["label"]).grid(row=row,column=0,sticky="w",pady=5)
        ttk.Entry(box,textvariable=fields[item["id"]],width=68).grid(row=row,column=1,padx=8)
        def choose(key=item["id"]):
            path=filedialog.askdirectory(parent=win)
            if path:fields[key].set(path)
        ttk.Button(box,text="선택",command=choose).grid(row=row,column=2)
    def close():
        win.destroy()
        if isinstance(parent,tk.Toplevel):parent.grab_set()
    def save():
        app.config["outgoing_folders"]={k:v.get().strip() for k,v in fields.items()}
        app.persist_config();app.commands.put(("config",app.config.copy()))
        if on_save:on_save()
        close()
    win.protocol("WM_DELETE_WINDOW",close)
    ttk.Button(box,text="기본 설정 저장",command=save).grid(row=6,column=1,sticky="e",pady=12)


class OutgoingEditor:
    def __init__(self,app,job,parent=None,on_save=None,on_close=None,enable=False):
        self.app,self.job,self.parent=app,job,parent or app.root
        self.on_save,self.on_close=on_save,on_close
        self.owner,self.request=uuid.uuid4().hex,""
        self.out=defaults(job,app.config)
        if enable:self.out["enabled"]=True
        self.loading=False
        self.win=tk.Toplevel(self.parent)
        self.win.title("발신 등록 · 직인날인대장 · 한글 공문 작성")
        self.win.geometry(f"{min(1250,self.win.winfo_screenwidth()-70)}x{min(900,self.win.winfo_screenheight()-100)}")
        self.win.minsize(930,680)
        self.win.transient(self.parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW",self.close)
        app.outgoing_editor=self
        outer=ttk.Frame(self.win,padding=16)
        outer.pack(fill="both",expand=True)
        self.enabled=tk.BooleanVar(value=self.out["enabled"])
        self.same=tk.BooleanVar(value=self.out["title_same"])
        ttk.Label(outer,text="발신 정보와 공문 내용을 준비하세요",font=("맑은 고딕",16,"bold")).pack(anchor="w")
        self.enable_control=ttk.Checkbutton(outer,text="수신과 함께 발신대장 + 직인날인대장에도 등록",variable=self.enabled)
        self.enable_control.pack(anchor="w",pady=8)
        notebook=ttk.Notebook(outer)
        notebook.pack(fill="both",expand=True)
        info=ttk.Frame(notebook,padding=14)
        content=ttk.Frame(notebook,padding=12)
        notebook.add(info,text="발신·직인 등록 정보")
        notebook.add(content,text="OCR 원문 · 공문 내용 작성")
        self.vars={key:tk.StringVar(value=str(self.out.get(key,""))) for key in ("title","date","receiver","sender","method","reference","template_path","purpose","template_id","save_folder","file_number","filename_suffix")}
        self.keep_body=tk.BooleanVar(value=self.out.get("keep_template_body",False))
        self.bodies_by_style=dict(self.out.get("template_bodies",{}))
        self.body_backups=dict(self.out.get("body_backups",{}))
        self.family=tk.StringVar(value=next(k for k,v in FAMILY_LABELS.items() if v==self.out["ledger_type"]))
        self.stamp={key:tk.StringVar(value=str(self.out["stamp"].get(key,""))) for key in ("department","applicant","person","date")}
        self.controls=[]
        def entry(label,key,row,colspan=3):
            ttk.Label(info,text=label).grid(row=row,column=0,sticky="w",pady=8)
            widget=ttk.Entry(info,textvariable=self.vars[key])
            widget.grid(row=row,column=1,columnspan=colspan,sticky="ew",padx=10)
            self.controls.append(widget)
            return widget
        ttk.Label(info,text="발신 등록대장").grid(row=0,column=0,sticky="w",pady=8)
        family=ttk.Combobox(info,textvariable=self.family,values=list(FAMILY_LABELS),state="readonly")
        family.grid(row=0,column=1,sticky="ew",padx=10)
        self.controls.append(family)
        entry("발신 제목","title",1)
        same=ttk.Checkbutton(info,text="수신 제목과 동일 (제목을 직접 수정하면 별도 제목으로 사용)",variable=self.same,command=self.use_receipt_title)
        same.grid(row=2,column=1,columnspan=3,sticky="w",padx=10)
        self.controls.append(same)
        entry("수신 기관","receiver",3)
        entry("발신일 (YYYY-MM-DD)","date",4,1)
        ttk.Label(info,text="발송자").grid(row=4,column=2,sticky="e")
        sender=ttk.Entry(info,textvariable=self.vars["sender"])
        sender.grid(row=4,column=3,sticky="ew",padx=10)
        self.controls.append(sender)
        ttk.Label(info,text="발신 방법").grid(row=5,column=0,sticky="w",pady=8)
        method=ttk.Combobox(info,textvariable=self.vars["method"],values=METHODS,state="readonly")
        method.grid(row=5,column=1,sticky="ew",padx=10)
        self.controls.append(method)
        def family_changed(_=None):
            values=METHODS if FAMILY_LABELS[self.family.get()]=="공문" else tuple(x for x in METHODS if x!="문서24")
            method.configure(values=values)
            if self.vars["method"].get() not in values:self.vars["method"].set("Email")
        family.bind("<<ComboboxSelected>>",family_changed)
        family_changed()
        ttk.Label(info,text="발신번호: "+self.out.get("document_number","등록 후 자동 결정")).grid(row=6,column=0,columnspan=4,sticky="w",pady=8)
        stamp_box=ttk.LabelFrame(info,text="직인날인대장에 함께 적을 내용",padding=12)
        stamp_box.grid(row=7,column=0,columnspan=4,sticky="ew",pady=12)
        self.stamp_controls=[]
        for i,(key,label) in enumerate((("department","신청부서"),("applicant","신청자"),("date","날인일자"),("person","날인자 (기본 공란)"))):
            ttk.Label(stamp_box,text=label).grid(row=i,column=0,sticky="w",pady=5)
            widget=ttk.Entry(stamp_box,textvariable=self.stamp[key])
            widget.grid(row=i,column=1,sticky="ew",padx=10)
            self.stamp_controls.append(widget)
        stamp_box.columnconfigure(1,weight=1)
        ttk.Label(info,text="대장 등록 기능이며, 메일 발송이나 문서에 직인을 찍는 작업은 하지 않습니다.").grid(row=8,column=0,columnspan=4,sticky="w",pady=8)
        info.columnconfigure(1,weight=2)
        info.columnconfigure(3,weight=1)
        # Content uses the original OCR text separately from the editable outgoing draft.
        panes=ttk.PanedWindow(content,orient="horizontal")
        panes.pack(fill="both",expand=True)
        left,right=ttk.Frame(panes),ttk.Frame(panes,padding=(12,0,0,0))
        panes.add(left,weight=1);panes.add(right,weight=1)
        self.file_var=tk.StringVar(value="1. "+Path(job["files"][0]["path"]).name)
        self.file_box=ttk.Combobox(left,textvariable=self.file_var,values=[f"{i+1}. {Path(f['path']).name}" for i,f in enumerate(job["files"])],state="readonly")
        self.file_box.pack(fill="x")
        self.file_box.bind("<<ComboboxSelected>>",self.load_source)
        self.source=tk.Text(left,wrap="word",height=18,font=("맑은 고딕",10),padx=8,pady=8)
        self.source.pack(fill="both",expand=True,pady=6)
        self.source.insert("1.0",job.get("text",""));self.source.configure(state="disabled")
        source_buttons=ttk.Frame(left);source_buttons.pack(fill="x")
        ttk.Button(source_buttons,text="OCR 원문 복사",command=lambda:self.copy(self.source.get("1.0","end"))).pack(side="left")
        ttk.Button(source_buttons,text="줄바꿈 정리해 복사",command=lambda:self.copy(reflow(self.source.get("1.0","end")))).pack(side="left",padx=5)
        ttk.Button(left,text="원문에서 선택한 글을 본문에 넣기",command=self.insert_selected).pack(anchor="w",pady=6)
        ttk.Button(left,text="필요 본문 추출 · 띄어쓰기 정리",command=self.prepare_excerpt).pack(anchor="w",pady=4)
        ttk.Combobox(right,textvariable=self.vars["purpose"],values=PURPOSES,state="readonly").pack(fill="x")
        ttk.Label(right,text="관련 문서 (선택)").pack(anchor="w",pady=(5,0))
        ttk.Entry(right,textvariable=self.vars["reference"]).pack(fill="x")
        ttk.Label(right,text="핵심 전달·회신 내용 — 실제 내용을 입력하세요").pack(anchor="w",pady=(5,0))
        self.summary=tk.Text(right,height=3,wrap="word",font=("맑은 고딕",10))
        self.summary.insert("1.0",self.out.get("summary",""));self.summary.pack(fill="x",pady=3)
        ttk.Button(right,text="입력 내용으로 공문 초안 구성",command=self.compose).pack(anchor="w",pady=4)
        self.body=tk.Text(right,height=10,wrap="word",font=("맑은 고딕",11),padx=8,pady=8)
        self.body.insert("1.0",self.out.get("body",""));self.body.pack(fill="both",expand=True,pady=4)
        buttons=ttk.Frame(right);buttons.pack(fill="x")
        ttk.Button(buttons,text="본문 복사",command=lambda:self.copy(self.body.get("1.0","end"))).pack(side="left")
        ttk.Button(buttons,text="공문 전체 복사",command=lambda:self.copy(full_text(self.job,self.current()))).pack(side="left",padx=5)
        templatebar=ttk.LabelFrame(outer,text="발신 HWP 양식 · 저장 위치",padding=6);templatebar.pack(fill="x",pady=(8,6))
        self.styles=hwp_docs.catalog()
        self.style_label=tk.StringVar(value=next((x["label"] for x in self.styles if x["id"]==self.vars["template_id"].get()),"양식 선택"))
        choice=ttk.Combobox(templatebar,textvariable=self.style_label,values=[x["label"] for x in self.styles],state="readonly",width=39)
        choice.grid(row=0,column=0,sticky="ew",padx=4)
        choice.bind("<<ComboboxSelected>>",self.choose_style)
        ttk.Label(templatebar,text="발신번호 숫자").grid(row=0,column=1)
        ttk.Entry(templatebar,textvariable=self.vars["file_number"],width=8).grid(row=0,column=2,padx=4)
        ttk.Label(templatebar,text="파일명 뒤 표시").grid(row=0,column=3)
        ttk.Entry(templatebar,textvariable=self.vars["filename_suffix"],width=24).grid(row=0,column=4,padx=4)
        ttk.Entry(templatebar,textvariable=self.vars["save_folder"]).grid(row=1,column=0,columnspan=4,sticky="ew",padx=4,pady=4)
        ttk.Button(templatebar,text="저장 폴더 선택",command=self.choose_destination).grid(row=1,column=4)
        ttk.Button(templatebar,text="양식별 기본 저장 위치 설정",command=self.folder_settings).grid(row=2,column=0,sticky="w")
        ttk.Button(templatebar,text="별도 HWPX 양식 선택",command=self.choose_template).grid(row=2,column=4)
        ttk.Label(templatebar,text="HWP는 원본 폼·Ctrl 스타일·문단 서식 유지 · 본문 수정은 한글의 기존 문단에서").grid(row=3,column=0,columnspan=4,sticky="w")
        ttk.Button(templatebar,text="기본 본문 불러오기",command=lambda:self.use_template_body(True)).grid(row=3,column=4)
        templatebar.columnconfigure(0,weight=1)
        if self.out.get("number"):self.vars["file_number"].set(str(self.out["number"]))
        if not self.vars["template_id"].get():
            initial=hwp_docs.suggested_style(job,self.out["ledger_type"])
            self.style_label.set(initial["label"]);self.choose_style()
        elif not self.body.get("1.0","end").strip():self.use_template_body(True)
        actions=ttk.Frame(outer);actions.pack(fill="x")
        ttk.Button(actions,text="원본 폼 HWP 저장·열기",command=lambda:self.export(False)).pack(side="left")
        ttk.Button(actions,text="별도 HWPX 저장",command=self.export_hwpx).pack(side="left",padx=6)
        ttk.Button(actions,text="발신 설정·초안 저장",command=self.save).pack(side="right")
        self.status=tk.StringVar(value="6종 기본 양식 · 파일명은 발신번호/오늘 날짜/HWP 제목으로 생성합니다. HWP 저장에는 이 PC의 한컴 한글이 필요합니다.")
        ttk.Label(outer,textvariable=self.status,wraplength=1180).pack(fill="x",pady=(8,0))
        self.vars["title"].trace_add("write",self.title_changed)
        self.body.edit_modified(False)
        self.body.bind("<<Modified>>",self.body_changed)
        previous_sender=[self.vars["sender"].get()]
        def sender_changed(*_):
            if self.stamp["applicant"].get()==previous_sender[0] and not self.out["stamp"].get("sheet_attempted"):
                self.stamp["applicant"].set(self.vars["sender"].get())
            previous_sender[0]=self.vars["sender"].get()
        self.vars["sender"].trace_add("write",sender_changed)
        if self.out.get("sheet_attempted"):
            self.enable_control.configure(state="disabled")
            for control in self.controls:control.configure(state="disabled")
        if self.out["stamp"].get("sheet_attempted"):
            for control in self.stamp_controls:control.configure(state="disabled")

    def current(self):
        template_id=self.vars["template_id"].get()
        body=self.body.get("1.0","end").strip()
        keep=self.keep_body.get() and body==hwp_docs.template_body(template_id)
        self.bodies_by_style[template_id]={"body":body,"keep":keep}
        out={**self.out,**{k:v.get().strip() for k,v in self.vars.items()},"enabled":self.enabled.get(),"title_same":self.same.get(),
             "ledger_type":FAMILY_LABELS[self.family.get()],"summary":self.summary.get("1.0","end").strip(),"body":self.body.get("1.0","end").strip(),
             "keep_template_body":keep,"template_bodies":dict(self.bodies_by_style),"body_backups":dict(self.body_backups),
             "stamp":{**self.out["stamp"],**{k:v.get().strip() for k,v in self.stamp.items()}}}
        if out["title_same"] and not out.get("sheet_attempted"):out["title"]=self.job["title"]
        return out

    def use_receipt_title(self):
        if self.same.get():
            self.loading=True;self.vars["title"].set(self.job["title"]);self.loading=False

    def title_changed(self,*_):
        if not self.loading and self.vars["title"].get()!=self.job["title"]:self.same.set(False)

    def copy(self,text):
        self.win.clipboard_clear();self.win.clipboard_append(text.rstrip());self.win.update_idletasks()
        self.status.set("복사했습니다. 한글의 원하는 위치에서 Ctrl+V로 붙여넣으세요.")

    def insert_selected(self):
        try:text=self.source.get("sel.first","sel.last")
        except tk.TclError:return messagebox.showinfo("본문 선택","왼쪽 OCR 원문에서 필요한 글을 먼저 선택하세요.",parent=self.win)
        self.body.insert("insert",reflow(text))

    def prepare_excerpt(self):
        from ocr_text_editor import OcrTextEditor
        selected=False
        try:text=self.source.get("sel.first","sel.last");selected=True
        except tk.TclError:text=self.source.get("1.0","end")
        OcrTextEditor(self.app,text,parent=self.win,selected=selected,on_insert=lambda t:self.body.insert("insert",t),on_hwp=lambda:self.export(False))

    def compose(self):
        try:text=build_body(self.current())
        except ValueError as error:return messagebox.showerror("내용 확인",str(error),parent=self.win)
        self.body.delete("1.0","end");self.body.insert("1.0",text)
        self.status.set("입력한 실제 내용으로 초안을 구성했습니다. 저장 전에 검토·수정하세요.")

    def choose_template(self):
        path=filedialog.askopenfilename(parent=self.win,filetypes=[("한글 양식","*.hwpx *.hwp"),("모든 파일","*.*")])
        if path:self.vars["template_path"].set(path)

    def choose_style(self,_=None):
        item=next(x for x in self.styles if x["label"]==self.style_label.get())
        if self.out.get("sheet_attempted") and item["family"]!=self.out["ledger_type"]:
            self.style_label.set(next(x["label"] for x in self.styles if x["id"]==self.vars["template_id"].get()))
            return messagebox.showerror("등록 유형 확인","등록한 대장과 같은 유형의 양식을 선택해 주세요.",parent=self.win)
        previous=self.vars["template_id"].get()
        current=self.body.get("1.0","end").strip()
        if previous:self.bodies_by_style[previous]={"body":current,"keep":self.keep_body.get() and current==hwp_docs.template_body(previous)}
        self.vars["template_id"].set(item["id"])
        self.vars["save_folder"].set(hwp_docs.destination(self.app.config,item))
        self.vars["filename_suffix"].set(item["suffix"])
        if not self.out.get("sheet_attempted"):
            self.family.set(next(k for k,v in FAMILY_LABELS.items() if v==item["family"]))
        if not self.vars["receiver"].get() or (_ is not None and self.vars["receiver"].get() in [x["receiver"] for x in self.styles]):
            self.vars["receiver"].set(item["receiver"])
        if item["family"]!="공문" and self.vars["method"].get()=="문서24":self.vars["method"].set("Email")
        cached=self.bodies_by_style.get(item["id"])
        if cached:
            self.body.delete("1.0","end");self.body.insert("1.0",cached["body"]);self.keep_body.set(cached["keep"])
        elif previous or not current:
            self.body.delete("1.0","end");self.use_template_body(True)

    def use_template_body(self,force=False):
        if force:self.keep_body.set(True)
        key=self.vars["template_id"].get()
        if self.keep_body.get():
            original=hwp_docs.template_body(key)
            current=self.body.get("1.0","end").strip()
            if current and current!=original:self.body_backups[key]=current
            self.body.delete("1.0","end");self.body.insert("1.0",original)
        elif self.body_backups.get(key):
            self.body.delete("1.0","end");self.body.insert("1.0",self.body_backups[key])
        self.body.edit_modified(False)

    def body_changed(self,_=None):
        if not self.body.edit_modified():return
        if self.body.get("1.0","end").strip()!=hwp_docs.template_body(self.vars["template_id"].get()):self.keep_body.set(False)
        self.body.edit_modified(False)

    def choose_destination(self):
        path=filedialog.askdirectory(parent=self.win,title="이 공문을 저장할 폴더 선택",mustexist=True)
        if path:self.vars["save_folder"].set(path)

    def folder_settings(self):
        folder_settings_dialog(self.app,self.win,lambda:self.vars["save_folder"].set(hwp_docs.destination(self.app.config,hwp_docs.style(self.vars["template_id"].get()))))

    def load_source(self,_=None):
        self.request=uuid.uuid4().hex
        self.app.busy=True
        self.app.commands.put(("draft_source",{"owner":self.owner,"request":self.request,"file":self.job["files"][max(0,self.file_box.current())]}))

    def deliver(self,data):
        if data.get("owner")!=self.owner or data.get("request")!=self.request:return
        self.source.configure(state="normal");self.source.delete("1.0","end");self.source.insert("1.0",data["text"]);self.source.configure(state="disabled")

    def export(self,copy_only):
        out=self.current()
        try:
            item=hwp_docs.style(out["template_id"])
            name=hwp_docs.filename(item,out)
            if not out["save_folder"]:raise ValueError("저장 폴더를 선택해 주세요.")
            if not Path(out["save_folder"]).is_absolute():raise ValueError("저장 폴더는 절대 경로를 입력해 주세요.")
        except (ValueError,StopIteration) as error:return messagebox.showerror("HWP 저장 확인",str(error),parent=self.win)
        # Choosing the folder is separate from the derived filename, so filename and HWP title stay equal.
        self.app.busy=True
        self.app.commands.put(("draft_export",{"hash":self.job["hash"],"outgoing":out,"path":str(Path(out["save_folder"])/name),"copy_only":False,"native_hwp":True}))
        self.status.set("한글에서 선택한 양식으로 HWP를 저장하는 중… 최초 파일 접근 안내가 나오면 허용해 주세요.")

    def export_hwpx(self):
        out=self.current()
        if not Path(out["template_path"]).is_file():return messagebox.showinfo("기본 양식","기본 한글 양식 파일을 먼저 선택해 주세요.",parent=self.win)
        from folder_naming import safe_component
        suffix=".hwpx"
        path=filedialog.asksaveasfilename(parent=self.win,defaultextension=suffix,initialfile=safe_component(out["title"])+suffix,filetypes=[("한글 문서","*"+suffix)])
        if not path:return
        self.app.busy=True
        self.app.commands.put(("draft_export",{"hash":self.job["hash"],"outgoing":out,"path":path,"copy_only":False}))
        self.status.set("원본 양식을 보존하고 새 파일을 저장하는 중…")

    def save(self):
        out=self.current()
        for key in ("date",):
            try:date.fromisoformat(out[key])
            except ValueError:return messagebox.showerror("날짜 확인","발신일을 YYYY-MM-DD 형식으로 입력해 주세요.",parent=self.win)
        try:date.fromisoformat(out["stamp"]["date"])
        except ValueError:return messagebox.showerror("날짜 확인","날인일자를 YYYY-MM-DD 형식으로 입력해 주세요.",parent=self.win)
        if self.on_save:self.on_save(out)
        else:
            self.app.busy=True
            self.app.commands.put(("outgoing_save",{"hash":self.job["hash"],"outgoing":out,
                                                       "receipt_fields":{k:self.job.get(k,"공문" if k=="receipt_type" else "") for k in ("title","date","recipient","issuer","receipt_type")}}))
        self.close(saved=True)

    def close(self,saved=False):
        self.win.destroy();self.app.outgoing_editor=None
        if isinstance(self.parent,tk.Toplevel) and self.parent.winfo_exists():self.parent.grab_set()
        if self.on_close:self.on_close(saved)
