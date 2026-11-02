"""Guided native desktop workflow. All writes still pass through the serial worker."""
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import uuid

from queue_visibility import missing_download_source
from outgoing_editor import OutgoingEditor, FAMILY_LABELS
from outgoing import defaults, validate, METHODS
import hwp_documents as hwp_docs


def heading(parent, title, hint=""):
    ttk.Label(parent, text=title, font=("맑은 고딕", 18, "bold")).pack(anchor="w", pady=(8, 4))
    if hint:ttk.Label(parent, text=hint, foreground="#667085", wraplength=1000).pack(anchor="w", pady=(0, 12))


def table(parent, columns, extended=False):
    frame=ttk.Frame(parent);frame.pack(fill="both", expand=True)
    tree=ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="extended" if extended else "browse", height=8)
    for key,label,width in columns:
        tree.heading(key,text=label);tree.column(key,width=width,minwidth=65,stretch=key=="title")
    scroll=ttk.Scrollbar(frame,command=tree.yview);tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")
    return tree


class WorkflowShell:
    def __init__(self, app, outer):
        self.app=app;self.section="receipt";self.step=1;self.current=None
        self.pending_register=None;self.draft_active=False;self.drafts={}
        self.continue_outgoing=tk.BooleanVar(value=False)
        header=ttk.Frame(outer);header.pack(fill="x")
        ttk.Label(header,text="서울센터 공문 업무",font=("맑은 고딕",22,"bold")).pack(side="left")
        ttk.Button(header,text="설정 · 연결",command=app.settings).pack(side="right")
        nav=ttk.Frame(outer);nav.pack(fill="x",pady=14)
        self.nav={}
        for key,label,fn in (("receipt","수신 접수",self.receipt_home),("outgoing","발신 작성",self.outgoing_home),("history","처리 이력",self.history_home)):
            button=ttk.Button(nav,text=label,command=fn);button.pack(side="left",padx=(0,8));self.nav[key]=button
        app.watching=tk.BooleanVar(value=False)
        ttk.Checkbutton(nav,text="다운로드 자동 확인",variable=app.watching,command=app.toggle_watch).pack(side="right")
        self.steps=tk.StringVar();ttk.Label(outer,textvariable=self.steps,foreground="#1c5295",font=("맑은 고딕",11,"bold")).pack(anchor="w",pady=(0,8))
        self.content=ttk.Frame(outer);self.content.pack(fill="both",expand=True)
        self.pages={key:ttk.Frame(self.content) for key in ("queue","title","route","receipt_result","outgoing_list","outgoing_types","outgoing_form","outgoing_result","history")}
        app.fields={k:tk.StringVar() for k in ("title","category","recipient","date","issuer","receipt_type")}
        app.outgoing_enabled=tk.BooleanVar(value=False);app.detail=tk.StringVar()
        app.status=tk.StringVar(value="같은 메일에서 받은 첨부파일을 선택하고 다음으로 진행하세요.")
        app.update_status=tk.StringVar();app.auth_status=tk.StringVar(value="브라우저 로그인 방식 · API 설정 불필요" if app.config.get("auth_mode")=="browser" else "Google 계정 연결")
        ttk.Label(outer,textvariable=app.status,foreground="#1c5295",wraplength=1000).pack(fill="x",pady=(10,2))
        ttk.Label(outer,textvariable=app.update_status,foreground="#667085").pack(anchor="w")
        self.build_queue();self.build_history();self.show("queue","receipt",1)

    def available(self):
        app=self.app
        return not (app.busy or app.updating or app.startup_gate or (hasattr(app,"worker") and app.worker.working.is_set()))

    def clear_editor(self):
        for name in ("title_dialog","route_dialog","outgoing_editor"):
            editor=getattr(self.app,name,None)
            if editor is not None and getattr(editor,"embedded",False):
                if name=="outgoing_editor":
                    self.drafts[editor.job["hash"]]=editor.current()
                editor.close()

    def show(self,page,section,step=1):
        for frame in self.pages.values():frame.pack_forget()
        self.pages[page].pack(fill="both",expand=True)
        self.section,self.step=section,step
        labels=("첨부파일","제목 확인","저장 위치","접수 완료") if section=="receipt" else ("수신 문서","양식 선택","발신 정보","등록 · HWP 저장")
        self.steps.set("   →   ".join(("● " if i==step else "")+label for i,label in enumerate(labels,1)) if section!="history" else "등록된 문서와 저장 결과")
        for key,button in self.nav.items():button.configure(state="disabled" if key==section else "normal")
        self.draft_active=page in ("title","route","outgoing_types","outgoing_form")

    def empty(self,page):
        for widget in self.pages[page].winfo_children():widget.destroy()
        return self.pages[page]

    def build_queue(self):
        page=self.pages["queue"]
        heading(page,"1. 첨부파일을 선택하세요","같은 메일의 본 공문과 붙임을 함께 선택하세요. 여러 파일은 Ctrl 또는 Shift로 선택할 수 있습니다. ZIP 안의 문서도 표시됩니다.")
        bar=ttk.Frame(page);bar.pack(fill="x",pady=(0,10))
        ttk.Button(bar,text="파일 추가",command=self.app.add_files).pack(side="left")
        ttk.Button(bar,text="목록 삭제",command=self.app.ignore).pack(side="left",padx=8)
        self.app.tree=table(page,[("state","상태",155),("category","분류",145),("title","공문 제목 / 파일명",500),("number","접수번호",100)],True)
        self.app.tree.bind("<<TreeviewSelect>>",self.app.select)
        bottom=ttk.Frame(page);bottom.pack(fill="x",pady=(12,0))
        ttk.Label(bottom,text="목록 삭제는 파일을 지우지 않습니다. 이동·삭제된 다운로드는 목록에서 자동 제외됩니다.",foreground="#667085").pack(side="left")
        ttk.Button(bottom,text="다음: 제목 확인",command=self.start_receipt).pack(side="right")

    def build_history(self):
        for name in ("history","outgoing_list"):
            page=self.pages[name]
            heading(page,"처리 이력" if name=="history" else "발신할 수신 문서를 고르세요","수신 접수가 끝난 문서에서 발신 공문을 이어서 작성할 수 있습니다. 수신 접수는 반복하지 않습니다.")
            tree=table(page,[("date","접수일",110),("title","제목",520),("number","수신 / 발신번호",175),("state","상태",180)])
            setattr(self,name+"_tree",tree)
            tree.bind("<<TreeviewSelect>>",self.app.select)
            bar=ttk.Frame(page);bar.pack(fill="x",pady=12)
            if name=="history":ttk.Button(bar,text="본문 · 저장 결과",command=self.app.preview).pack(side="left")
            ttk.Button(bar,text="이 문서로 발신 작성",command=self.start_outgoing_selected).pack(side="right")

    def selected_jobs(self):
        if self.section=="history":keys=self.history_tree.selection()
        elif self.section=="outgoing" and self.step==1:keys=self.outgoing_list_tree.selection()
        elif self.current and self.step>1:keys=[self.current]
        else:keys=self.app.tree.selection()
        return [self.app.jobs[k].copy() for k in keys if k in self.app.jobs]

    def focus(self,key):
        self.current=key
        for tree in (self.app.tree,self.history_tree,self.outgoing_list_tree):
            if tree.exists(key):tree.selection_set(key);tree.see(key)
        self.app.select()

    def update_job(self,job):
        key=job["hash"]
        alias=job.get("archive_alias") or job["state"] in ("묶음에 포함","묶음 처리 완료")
        received=bool(job.get("receipt_done") or job["state"]=="완료")
        hidden=alias or job.get("list_hidden") or job["state"]=="제외" or received or missing_download_source(job,self.app.config.get("downloads"))
        vals=(job["state"],job.get("category") or "확인 필요",job["title"],job.get("number",""))
        self.row(self.app.tree,key,not hidden,vals)
        out=job.get("outgoing",{})
        numbers=str(job.get("number", ""))+(" / "+str(out["number"]) if out.get("number") else "")
        vals=(job.get("date",""),job["title"],numbers,job["state"])
        for tree in (self.history_tree,self.outgoing_list_tree):self.row(tree,key,received and not alias,vals)

    @staticmethod
    def row(tree,key,visible,values):
        if not visible:
            if tree.exists(key):tree.delete(key)
        elif tree.exists(key):tree.item(key,values=values)
        else:tree.insert("",0,iid=key,values=values)

    def receipt_home(self):
        if not self.available():return
        self.clear_editor();self.current=None;self.show("queue","receipt",1)

    def history_home(self):
        if not self.available():return
        self.clear_editor();self.current=None;self.show("history","history")

    def outgoing_home(self):
        if not self.available():return
        self.clear_editor();self.current=None;self.show("outgoing_list","outgoing",1)

    def start_receipt(self):
        if not self.available():return
        jobs=self.selected_jobs()
        if not jobs:return messagebox.showinfo("첨부파일 선택","접수할 첨부파일을 선택하세요.")
        self.continue_outgoing.set(False)
        self.current=jobs[0]["hash"];self.app.select()
        if any(j.get("files_started") or j.get("sheet_attempted") for j in jobs):
            if len(jobs)>1:return messagebox.showinfo("재시도","이미 저장을 시작한 공문은 한 건씩 이어서 처리하세요.")
            return self.resume_receipt(jobs[0])
        self.title_step(jobs)

    def resume_receipt(self,job):
        page=self.empty("route");self.show("route","receipt",3)
        heading(page,"이전에 시작한 접수를 이어서 처리합니다",job.get("reason",""))
        ttk.Label(page,text=job["title"],wraplength=1000).pack(anchor="w",pady=12)
        ttk.Label(page,text="이미 저장된 위치·제목·등록 표시를 기준으로 확인하고 재시도합니다.").pack(anchor="w")
        ttk.Button(page,text="저장 결과 확인",command=self.app.preview).pack(anchor="w",pady=12)
        if not job.get("files_started") and not job.get("sheet_attempted"):
            ttk.Button(page,text="저장 위치 다시 선택",command=self.route_step).pack(anchor="w",pady=8)
        ttk.Button(page,text="접수 재시도",command=lambda:self.app.send("process",[job])).pack(anchor="e",pady=12)

    def recover(self):
        """A failed worker request must leave a usable screen, even if its panel already closed."""
        self.pending_register=None
        if self.section=="receipt" and self.current:
            if self.step==3 and getattr(self.app,"route_dialog",None) is None:
                self.resume_receipt(self.app.jobs[self.current])
            elif self.step==2 and getattr(self.app,"title_dialog",None) is None:
                self.current=None;self.show("queue","receipt",1)

    def title_step(self,jobs=None):
        if not self.available():return
        jobs=jobs if isinstance(jobs,list) else self.selected_jobs()
        if not jobs:return
        self.clear_editor();page=self.empty("title");self.show("title","receipt",2)
        meta=ttk.Frame(page);meta.pack(fill="x",pady=(4,8))
        from folder_naming import AGENCIES
        for key,label,options in (("category","공문 기관",list(AGENCIES)),("receipt_type","수신 종류",["공문","업무연락","처리지침"]),("recipient","담당자",None),("date","접수일",None)):
            ttk.Label(meta,text=label).pack(side="left",padx=(5,4))
            var=self.app.fields[key]
            if not var.get():var.set("공문" if key=="receipt_type" else self.app.config.get("recipient","") if key=="recipient" else date.today().isoformat() if key=="date" else "")
            widget=ttk.Combobox(meta,textvariable=var,values=options,state="readonly",width=16) if options else ttk.Entry(meta,textvariable=var,width=12)
            widget.pack(side="left",fill="x",expand=key=="category")
        from title_chooser import TitleChooser
        self.app.title_dialog=TitleChooser(self.app,jobs,parent=page)

    def route_step(self):
        self.clear_editor();page=self.empty("route");self.show("route","receipt",3)
        from route_chooser import RouteChooser
        self.app.route_dialog=RouteChooser(self.app,self.app.jobs[self.current].copy(),parent=page)

    def completed(self,job):
        self.focus(job["hash"])
        if self.section=="outgoing":return self.outgoing_result(job)
        page=self.empty("receipt_result");self.show("receipt_result","receipt",4)
        heading(page,"수신 접수가 완료되었습니다","아래 번호를 인쇄한 공문에 직접 적어 주세요.")
        ttk.Label(page,text="접수번호",foreground="#667085").pack(anchor="w",pady=(14,0))
        ttk.Label(page,text=str(job["number"]),foreground="#1c5295",font=("맑은 고딕",46,"bold")).pack(anchor="w")
        ttk.Label(page,text=job["title"],font=("맑은 고딕",13,"bold"),wraplength=1000).pack(anchor="w",pady=12)
        paths=job.get("destinations",[])
        box=tk.Text(page,height=6,wrap="word",font=("맑은 고딕",10));box.pack(fill="both",expand=True,pady=12)
        box.insert("1.0","저장 위치\n"+"\n".join(map(str,paths)));box.configure(state="disabled")
        bar=ttk.Frame(page);bar.pack(fill="x")
        ttk.Button(bar,text="다른 공문 접수",command=self.receipt_home).pack(side="left")
        ttk.Button(bar,text="이 공문으로 발신 작성"+(" →" if self.continue_outgoing.get() else ""),command=lambda:self.start_outgoing(job)).pack(side="right")

    def start_outgoing_selected(self):
        jobs=self.selected_jobs()
        if len(jobs)!=1:return messagebox.showinfo("수신 문서 선택","발신에 사용할 수신 문서를 한 개 선택하세요.")
        self.start_outgoing(jobs[0])

    def start_outgoing(self,job):
        if not self.available():return
        self.clear_editor();self.focus(job["hash"])
        if job.get("outgoing",{}).get("sheet_attempted"):return self.outgoing_form(job)
        page=self.empty("outgoing_types");self.show("outgoing_types","outgoing",2)
        heading(page,"어떤 공문을 작성하시나요?",job["title"])
        self.style_choice=tk.StringVar(value=job.get("outgoing",{}).get("template_id") or hwp_docs.suggested_style(job)["id"])
        for item in hwp_docs.catalog():
            line=ttk.Frame(page,padding=10);line.pack(fill="x",pady=4)
            ttk.Radiobutton(line,text=item["label"],value=item["id"],variable=self.style_choice).pack(side="left")
            ttk.Label(line,text=item["family"]+" · "+item["folder"],foreground="#667085").pack(side="right")
        bar=ttk.Frame(page);bar.pack(fill="x",pady=16)
        ttk.Button(bar,text="이전: 수신 문서",command=self.outgoing_home).pack(side="left")
        ttk.Button(bar,text="다음: 발신 정보",command=lambda:self.outgoing_form(self.app.jobs[self.current],self.style_choice.get())).pack(side="right")

    def outgoing_form(self,job,style_id=None):
        self.clear_editor();page=self.empty("outgoing_form");self.show("outgoing_form","outgoing",3)
        if job["hash"] in self.drafts:
            # Keep unsaved edits while moving between document steps; persisted ledger fields remain authoritative.
            from outgoing import merge_edit
            job={**job,"outgoing":dict(job.get("outgoing",{}))}
            merge_edit(job,self.drafts[job["hash"]])
        editor=SimpleOutgoing(self.app,job,page,style_id)
        self.app.outgoing_editor=editor

    def outgoing_saved(self,key):
        self.drafts.pop(key,None)
        self.focus(key)
        if self.pending_register==key:
            self.pending_register=None
            self.app.commands.put(("process",[self.app.jobs[key].copy()]))
            self.app.busy=True

    def outgoing_result(self,job):
        self.clear_editor();page=self.empty("outgoing_result");self.show("outgoing_result","outgoing",4)
        out=job.get("outgoing",{});heading(page,"발신대장 · 직인날인대장 등록 완료",out.get("title",job["title"]))
        ttk.Label(page,text=out.get("document_number") or out.get("number", ""),font=("맑은 고딕",30,"bold"),foreground="#1c5295").pack(anchor="w",pady=18)
        ttk.Label(page,text="직인 분류: "+out.get("ledger_type","공문")+" ○  ·  신청부서: "+out.get("stamp",{}).get("department","편의증진부")).pack(anchor="w")
        try:name=hwp_docs.filename(hwp_docs.style(out["template_id"]),out)
        except (ValueError,KeyError,StopIteration):name="양식과 저장 위치를 확인하세요."
        ttk.Label(page,text="HWP 저장 예정\n"+str(Path(out.get("save_folder", ""))/name),wraplength=1000).pack(anchor="w",pady=22)
        ttk.Label(page,text="한글을 열어 원본 양식의 기존 문단에서 수정하세요. Ctrl 스타일·들여쓰기·본문 서식을 유지합니다.",wraplength=1000).pack(anchor="w")
        ttk.Button(page,text="HWP 저장하고 한글 열기",command=lambda:self.export_registered(job)).pack(anchor="w",pady=16)
        ttk.Button(page,text="발신 정보 · 저장 위치 확인",command=lambda:self.outgoing_form(self.app.jobs[job["hash"]])).pack(anchor="w",pady=6)
        ttk.Button(page,text="다른 문서 작성",command=self.outgoing_home).pack(anchor="e",side="bottom",pady=12)

    def export_registered(self,job):
        # Use the ledger-authoritative saved metadata, never a suggested or sample number.
        out=job["outgoing"]
        try:
            name=hwp_docs.filename(hwp_docs.style(out["template_id"]),out)
            folder=Path(out.get("save_folder",""))
            if not folder.is_absolute():raise ValueError("발신 정보에서 저장 폴더를 선택하세요.")
        except (ValueError,KeyError,StopIteration) as error:return messagebox.showerror("HWP 저장 확인",str(error))
        self.app.send("draft_export",{"hash":job["hash"],"outgoing":out,"path":str(folder/name),"copy_only":False,"native_hwp":True})


class SimpleOutgoing(OutgoingEditor):
    """Keep the proven drafting methods; expose only fields needed for this document."""
    def __init__(self,app,job,parent,style_id=None):
        self.app,self.job,self.parent=app,job.copy(),app.root
        self.embedded=True;self.on_save=self.on_close=None
        self.owner,self.request=uuid.uuid4().hex,"";self.loading=False
        self.out=defaults(job,app.config);self.out["enabled"]=True
        self.win=ttk.Frame(parent,padding=12);self.win.pack(fill="both",expand=True)
        self.enabled=tk.BooleanVar(value=True);self.same=tk.BooleanVar(value=self.out["title_same"])
        self.vars={key:tk.StringVar(value=str(self.out.get(key,""))) for key in ("title","date","receiver","sender","method","reference","template_path","purpose","template_id","save_folder","file_number","filename_suffix")}
        self.keep_body=tk.BooleanVar(value=self.out.get("keep_template_body",False))
        self.bodies_by_style=dict(self.out.get("template_bodies",{}));self.body_backups=dict(self.out.get("body_backups",{}))
        self.family=tk.StringVar(value=next(k for k,v in FAMILY_LABELS.items() if v==self.out["ledger_type"]))
        self.stamp={key:tk.StringVar(value=str(self.out["stamp"].get(key,""))) for key in ("department","applicant","person","date")}
        self.styles=hwp_docs.catalog();self.style_label=tk.StringVar()
        # The editable draft is retained separately from the original native HWP paragraphs.
        self.body=tk.Text(self.win);self.body.insert("1.0",self.out.get("body",""))
        self.summary=tk.Text(self.win);self.summary.insert("1.0",self.out.get("summary",""))
        self.source=tk.Text(self.win);self.source.insert("1.0",job.get("text",""))
        item=hwp_docs.style(style_id or self.vars["template_id"].get() or hwp_docs.suggested_style(job)["id"])
        self.style_label.set(item["label"])
        if style_id or not self.vars["template_id"].get():self.choose_style(True)
        elif not self.body.get("1.0","end").strip():self.use_template_body(True)
        heading(self.win,item["label"],"수신 제목을 기본으로 가져옵니다. 필요한 항목만 수정하세요.")
        ttk.Label(self.win,text="발신대장: "+self.family.get()+"  ·  직인 분류: "+item["family"]+" ○",foreground="#1c5295").pack(anchor="w",pady=(0,12))
        fields=ttk.Frame(self.win);fields.pack(fill="x")
        self.controls=[]
        for row,(key,label) in enumerate((("title","발신 제목"),("receiver","수신 기관"),("date","발신일"),("sender","담당자"))):
            ttk.Label(fields,text=label).grid(row=row,column=0,sticky="w",pady=8)
            widget=ttk.Entry(fields,textvariable=self.vars[key]);widget.grid(row=row,column=1,sticky="ew",padx=12)
            self.controls.append(widget)
        fields.columnconfigure(1,weight=1)
        same=ttk.Checkbutton(self.win,text="수신 제목과 동일",variable=self.same,command=self.use_receipt_title);same.pack(anchor="w",padx=80,pady=4);self.controls.append(same)
        ttk.Label(self.win,text="저장 폴더").pack(anchor="w",pady=(12,2))
        route=ttk.Frame(self.win);route.pack(fill="x")
        ttk.Entry(route,textvariable=self.vars["save_folder"]).pack(side="left",fill="x",expand=True)
        ttk.Button(route,text="변경",command=self.choose_destination).pack(side="right",padx=6)
        detail=ttk.Frame(self.win)
        def toggle_details():
            win=tk.Toplevel(self.app.root);win.title("직인 신청정보 · 발신 방법");win.transient(self.app.root);win.grab_set()
            panel=ttk.Frame(win,padding=18);panel.pack(fill="both",expand=True)
            for row,(key,label) in enumerate((("department","신청부서"),("applicant","신청자"),("person","날인자 (공란 가능)"),("date","날인일자"))):
                ttk.Label(panel,text=label).grid(row=row,column=0,sticky="w",pady=7)
                entry=ttk.Entry(panel,textvariable=self.stamp[key],width=35);entry.grid(row=row,column=1,padx=12)
                if self.out["stamp"].get("sheet_attempted"):entry.configure(state="disabled")
            ttk.Label(panel,text="발신 방법").grid(row=4,column=0,sticky="w")
            method=ttk.Combobox(panel,textvariable=self.vars["method"],values=METHODS if item["family"]=="공문" else [x for x in METHODS if x!="문서24"],state="readonly")
            method.grid(row=4,column=1,sticky="ew",padx=12,pady=7)
            if self.out.get("sheet_attempted"):method.configure(state="disabled")
            ttk.Button(panel,text="적용",command=win.destroy).grid(row=5,column=1,sticky="e",pady=12)
        ttk.Button(self.win,text="직인 신청정보 · 발신 방법",command=toggle_details).pack(anchor="w",pady=10)
        self.stamp_controls=[]
        for row,(key,label) in enumerate((("department","신청부서"),("applicant","신청자"),("person","날인자 (공란 가능)"),("date","날인일자"))):
            ttk.Label(detail,text=label).grid(row=row,column=0,sticky="w",pady=3)
            widget=ttk.Entry(detail,textvariable=self.stamp[key]);widget.grid(row=row,column=1,sticky="ew",padx=12);self.stamp_controls.append(widget)
        ttk.Label(detail,text="발신 방법").grid(row=4,column=0,sticky="w")
        method=ttk.Combobox(detail,textvariable=self.vars["method"],values=METHODS if item["family"]=="공문" else [x for x in METHODS if x!="문서24"],state="readonly");method.grid(row=4,column=1,sticky="ew",padx=12);self.controls.append(method)
        detail.columnconfigure(1,weight=1)
        source=ttk.Frame(self.win);source.pack(fill="x",pady=8)
        self.file_var=tk.StringVar(value="1. "+Path(job["files"][0]["path"]).name)
        self.file_box=ttk.Combobox(source,textvariable=self.file_var,values=[f"{i+1}. {Path(f['path']).name}" for i,f in enumerate(job["files"])],state="readonly")
        self.file_box.pack(side="left",fill="x",expand=True);self.file_box.bind("<<ComboboxSelected>>",self.load_source)
        ttk.Button(source,text="OCR 본문 추출 · 띄어쓰기 정리",command=self.prepare_excerpt).pack(side="right",padx=6)
        self.status=tk.StringVar(value="대장에서 실제 발신번호를 받은 뒤 원본 양식의 HWP를 저장하고 한글에서 편집합니다.")
        ttk.Label(self.win,textvariable=self.status,wraplength=970).pack(anchor="w",pady=8)
        actions=ttk.Frame(self.win);actions.pack(side="bottom",fill="x",pady=12)
        if not self.out.get("sheet_attempted"):ttk.Button(actions,text="이전: 양식 선택",command=lambda:app.workflow.start_outgoing(job)).pack(side="left")
        ttk.Button(actions,text="작성 정보 저장",command=self.save).pack(side="left",padx=8)
        done=self.out.get("complete") and self.out.get("stamp",{}).get("complete")
        ttk.Button(actions,text="HWP 저장하고 한글 열기" if done else "발신대장 · 직인날인대장 등록",command=(lambda:self.export(False)) if done else self.register).pack(side="right")
        self.vars["title"].trace_add("write",self.title_changed)
        old_sender=[self.vars["sender"].get()]
        def sender_changed(*_):
            if self.stamp["applicant"].get()==old_sender[0] and not self.out["stamp"].get("sheet_attempted"):self.stamp["applicant"].set(self.vars["sender"].get())
            old_sender[0]=self.vars["sender"].get()
        self.vars["sender"].trace_add("write",sender_changed)
        if self.out.get("sheet_attempted"):
            for widget in self.controls:widget.configure(state="disabled")
        if self.out.get("stamp",{}).get("sheet_attempted"):
            for widget in self.stamp_controls:widget.configure(state="disabled")

    def register(self):
        if not self.app.workflow.available():return
        out=self.current()
        try:
            validate({**self.job,"outgoing":out},self.app.config)
            if not Path(out.get("save_folder","")).is_absolute():raise ValueError("HWP를 저장할 공유폴더를 선택하세요.")
        except ValueError as error:return messagebox.showerror("발신 정보 확인",str(error),parent=self.app.root)
        self.app.workflow.pending_register=self.job["hash"]
        self.save()

    def prepare_excerpt(self):
        if not self.app.workflow.available():return
        text=self.source.get("1.0","end").strip()
        if not text:
            self.open_excerpt_when_loaded=True
            return self.load_source()
        from ocr_text_editor import OcrTextEditor
        OcrTextEditor(self.app,text,parent=self.app.root)

    def deliver(self,data):
        if data.get("owner")!=self.owner or data.get("request")!=self.request:return
        super().deliver(data)
        if getattr(self,"open_excerpt_when_loaded",False):
            self.open_excerpt_when_loaded=False
            # Worker idle arrives after the result; open after its queue has drained.
            self.app.root.after(300,self.prepare_excerpt)

    def save(self):
        if not self.app.workflow.available():return
        out=self.current()
        try:
            date.fromisoformat(out["date"]);date.fromisoformat(out["stamp"]["date"])
        except ValueError:
            self.app.workflow.pending_register=None
            return messagebox.showerror("날짜 확인","날짜를 YYYY-MM-DD 형식으로 입력하세요.")
        if not self.app.send("outgoing_save",{"hash":self.job["hash"],"outgoing":out}):self.app.workflow.pending_register=None

    def close(self,saved=False):
        self.win.destroy();self.app.outgoing_editor=None
