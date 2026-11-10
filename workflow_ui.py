"""Guided native desktop workflow. All writes still pass through the serial worker."""
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox,filedialog
import uuid
import os

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
        self.pending_register=None;self.pending_open=None;self.draft_active=False;self.drafts={}
        self.excerpt_request=None;self.receipt_drafts={};self.route_drafts={}
        self.continue_outgoing=tk.BooleanVar(value=False)
        header=ttk.Frame(outer,style='App.TFrame');header.pack(fill="x")
        ttk.Label(header,text="서울센터 공문 업무",font=("맑은 고딕",22,"bold"),style='App.TLabel').pack(side="left")
        ttk.Button(header,text="설정 · 연결",command=app.settings).pack(side="right")
        nav=ttk.Frame(outer,style='Card.TFrame',padding=10);nav.pack(fill="x",pady=14)
        self.nav={}
        for key,label,fn in (("receipt","수신 접수",self.receipt_home),("outgoing","발신 작성",self.outgoing_home),("history","처리 이력",self.history_home)):
            button=ttk.Button(nav,text=label,command=fn);button.pack(side="left",padx=(0,8));self.nav[key]=button
        self.back_button=ttk.Button(nav,text="← 이전 단계 (파일 취소)",command=self.back);self.back_button.pack(side="left",padx=4)
        ttk.Button(nav,text="선택 내용 다시 확인",command=self.reconfigure).pack(side="left",padx=4)
        app.watching=tk.BooleanVar(value=True)
        ttk.Checkbutton(nav,text="다운로드 자동 확인",variable=app.watching,command=app.toggle_watch).pack(side="right")
        self.steps=tk.StringVar();ttk.Label(outer,textvariable=self.steps,foreground="#1c5295",font=("맑은 고딕",11,"bold"),style='App.TLabel').pack(anchor="w",pady=(0,8))
        self.content=ttk.Frame(outer,style='Card.TFrame',padding=16)
        self.pages={key:ttk.Frame(self.content) for key in ("queue","title","route","receipt_result","outgoing_list","outgoing_types","outgoing_form","outgoing_result","history")}
        app.fields={k:tk.StringVar() for k in ("title","category","recipient","date","issuer","receipt_type")}
        app.outgoing_enabled=tk.BooleanVar(value=False);app.detail=tk.StringVar()
        app.status=tk.StringVar(value="같은 메일에서 받은 첨부파일을 선택하고 다음으로 진행하세요.")
        app.update_status=tk.StringVar();app.auth_status=tk.StringVar(value="브라우저 로그인 방식 · API 설정 불필요" if app.config.get("auth_mode")=="browser" else "Google 계정 연결")
        # Reserve the status area's requested height before allocating space to document pages.
        self.footer=ttk.Frame(outer,style='App.TFrame',padding=(0,8,0,2));self.footer.pack(side='bottom',fill='x')
        self.status_labels=[]
        for variable,color in ((app.status,'#1c5295'),(app.update_status,'#667085')):
            label=ttk.Label(self.footer,textvariable=variable,foreground=color,wraplength=1000,style='App.TLabel',padding=(0,2))
            label.pack(fill='x');self.status_labels.append(label)
        def fit_status(event):
            for label in self.status_labels:label.configure(wraplength=max(1,event.width-8))
        self.footer.bind('<Configure>',fit_status)
        self.content.pack(fill='both',expand=True)
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
                elif name=="title_dialog":
                    self.receipt_drafts[self.current]={"title":editor.title.get("1.0","end").strip(),"file":editor.file_box.current(),'relief_manual_title':editor.relief_manual_title}
                elif name=="route_dialog" and editor.records:
                    editor.children_changed()
                    self.route_drafts[self.current]={"agency":editor.agency.get(),"targets":editor.targets(),"folder_title":"","children":[],"relief":editor.relief_values(),'relief_category':editor.agency.get()=='완화공문'}
                editor.close()

    def show(self,page,section,step=1):
        for frame in self.pages.values():frame.pack_forget()
        self.pages[page].pack(fill="both",expand=True)
        self.section,self.step=section,step
        labels=("첨부파일","제목 확인","저장 위치","접수 완료") if section=="receipt" else ("새 발신 · 수신 문서","양식 선택","발신 정보","등록 · HWP 저장")
        self.steps.set("   →   ".join(("● " if i==step else "")+label for i,label in enumerate(labels,1)) if section!="history" else "등록된 문서와 저장 결과")
        for key,button in self.nav.items():button.configure(state="disabled" if key==section else "normal",style='NavSelected.TButton' if key==section else 'TButton')
        self.back_button.configure(state="disabled" if section=="history" or step==1 else "normal")
        self.draft_active=page in ("title","route","outgoing_types","outgoing_form")

    def empty(self,page):
        for widget in self.pages[page].winfo_children():widget.destroy()
        return self.pages[page]

    def build_queue(self):
        page=self.pages["queue"]
        heading(page,"1. 첨부파일을 선택하세요","같은 메일의 본 공문과 붙임을 함께 선택하세요. 여러 파일은 Ctrl 또는 Shift로 선택할 수 있습니다. ZIP 안의 문서도 표시됩니다.")
        bar=ttk.Frame(page);bar.pack(fill="x",pady=(0,10))
        ttk.Button(bar,text="파일 추가",command=self.app.add_files).pack(side="left")
        ttk.Button(bar,text="공문에 첨부 추가",command=self.add_attachments).pack(side="left",padx=5)
        ttk.Button(bar,text="목록 삭제",command=self.app.ignore).pack(side="left",padx=8)
        self.drop_zone=ttk.Label(page,text="파일을 여기로 끌어다 놓으세요 · ZIP·PDF·한글·엑셀·HTML·DWG·DXF 등 · 이미지 제외",style='Drop.TLabel')
        self.drop_zone.pack(fill="x",pady=(0,10))
        self.app.tree=table(page,[("state","상태",145),("category","분류",130),("title","공문 제목 / 파일명",420),("modified","수정 날짜 ▼",145),("number","접수번호",90)],True)
        def refresh_order():
            if getattr(self.app,'closing',False):return
            self.sort_queue()
            self.app.root.after(5000,refresh_order)
        self.app.root.after(5000,refresh_order)
        self.app.tree.bind("<<TreeviewSelect>>",self.app.select)
        bottom=ttk.Frame(page);bottom.pack(fill="x",pady=(12,0))
        bottom.pack_configure(side="bottom",before=self.app.tree.master)
        ttk.Label(bottom,text="목록 삭제는 파일을 지우지 않습니다. 이동·삭제된 다운로드는 목록에서 자동 제외됩니다.",foreground="#667085").pack(side="left")
        ttk.Button(bottom,text="다음: 제목 확인",command=self.start_receipt,style='Accent.TButton').pack(side="right")

    def build_history(self):
        for name in ("history","outgoing_list"):
            page=self.pages[name]
            heading(page,"처리 이력" if name=="history" else "새 발신을 작성하거나 수신 문서를 고르세요","새 발신 작성은 수신 없이 시작합니다. 기존 수신 문서를 선택하면 이어서 발신 공문을 작성합니다.")
            tree=table(page,[("date","접수 / 발신일",110),("title","제목",520),("number","수신 / 발신번호",175),("state","상태",180)],extended=name=='history')
            setattr(self,name+"_tree",tree)
            tree.bind("<<TreeviewSelect>>",self.app.select)
            bar=ttk.Frame(page);bar.pack(fill="x",pady=12)
            bar.pack_configure(side="bottom",before=tree.master)
            if name=="history":
                ttk.Button(bar,text="본문 · 저장 결과",command=self.app.preview).pack(side="left")
                ttk.Button(bar,text='목록 삭제',command=self.app.hide_history).pack(side='left',padx=6)
                tree.bind('<Delete>',lambda event:self.app.hide_history())
                tree.bind('<Control-a>',lambda event,t=tree:(t.selection_set(t.get_children('')),'break')[-1])
                ttk.Label(page,text='Ctrl·Shift로 여러 항목 선택 → 목록 삭제. 파일·시트·접수번호는 유지됩니다.',foreground='#667085').pack(fill='x',before=bar,pady=(6,0))
            else:
                self.new_outgoing_button=ttk.Button(bar,text='＋ 새 발신 작성',command=self.start_new_outgoing,style='Accent.TButton')
                self.new_outgoing_button.pack(side='left',padx=(0,8))
                ttk.Button(bar,text='발신 초기화 · 다시 시작',command=self.restart_outgoing).pack(side='left')
                ttk.Button(bar,text='목록에서 삭제',command=lambda:self.restart_outgoing(hide=True)).pack(side='left',padx=6)
                tree.bind('<Delete>',lambda event:self.restart_outgoing(hide=True))
            ttk.Button(bar,text="공문에 첨부 추가",command=self.add_attachments).pack(side="left",padx=6)
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
        from attachment_files import DOCUMENT_SUFFIXES
        document_files=any(Path(f.get('path','')).suffix.lower() in DOCUMENT_SUFFIXES for f in job.get('files',[]))
        hidden=alias or not document_files or job.get("list_hidden") or job["state"]=="제외" or (received and not job.get('files_rolled_back')) or missing_download_source(job,self.app.config.get("downloads"))
        queue_title=f"[ZIP] {Path(job['source_archive']).name} · {job['title']}" if job.get('source_archive') else job['title']
        vals=(job["state"],job.get("category") or "확인 필요",queue_title,self.modified_label(job),job.get("number",""))
        self.row(self.app.tree,key,not hidden,vals)
        self.sort_queue()
        out=job.get("outgoing",{})
        standalone=job.get('standalone_outgoing',False)
        numbers=str(out.get('number','')) if standalone else str(job.get("number", ""))+(" / "+str(out["number"]) if out.get("number") else "")
        vals=(job.get("date",""),job["title"] or ('새 발신 공문 (작성 중)' if standalone else ''),numbers,job["state"])
        listed=received or standalone
        self.row(self.history_tree,key,listed and not alias and not job.get('history_hidden'),vals)
        self.row(self.outgoing_list_tree,key,listed and not alias and not job.get('outgoing_list_hidden'),vals)

    def restart_outgoing(self,hide=False):
        if not self.available():return
        jobs=self.selected_jobs()
        if len(jobs)!=1:return messagebox.showinfo('발신 문서 선택','초기화하거나 목록에서 삭제할 문서 한 개를 선택하세요.')
        job=jobs[0]
        text=('이 문서의 발신 진행을 초기화하고 발신 목록에서 제외할까요?' if hide else '이 문서의 발신 진행을 초기화하고 양식 선택부터 다시 시작할까요?')
        text+='\n\n기존 발신번호·직인 등록 진행은 다시 사용하지 않습니다. 다음 등록 때 구글시트를 다시 읽어 새 발신번호를 받습니다.\n'+('기존 시트 행·저장된 파일은 그대로 유지합니다.' if job.get('standalone_outgoing') else '수신 접수·기존 시트 행·저장된 파일은 그대로 유지합니다.')
        if not messagebox.askyesno('발신 초기화 확인',text,parent=self.app.root):return
        self.app.send('outgoing_restart',{'hash':job['hash'],'expected_id':job.get('outgoing',{}).get('id'),'hide':hide})

    def outgoing_restarted(self,data):
        key=data['hash'];self.drafts.pop(key,None);self.pending_register=None;self.pending_open=None
        def ready():
            if not self.available():return self.app.root.after(50,ready)
            if data.get('hide'):self.outgoing_home()
            else:self.start_outgoing(self.app.jobs[key])
        self.app.root.after(50,ready)

    @staticmethod
    def row(tree,key,visible,values):
        if not visible:
            if tree.exists(key):tree.delete(key)
        elif tree.exists(key):tree.item(key,values=values)
        else:tree.insert("",0,iid=key,values=values)

    @staticmethod
    def modified_label(job):
        from queue_visibility import source_modified
        from datetime import datetime
        value=source_modified(job)
        return datetime.fromtimestamp(value/1_000_000_000).strftime('%Y-%m-%d %H:%M') if value else ''

    def sort_queue(self):
        from queue_visibility import source_modified
        tree=self.app.tree
        keys=tree.get_children('')
        for index,key in enumerate(sorted(keys,key=lambda k:source_modified(self.app.jobs.get(k,{})),reverse=True)):
            tree.move(key,'',index)
            tree.set(key,'modified',self.modified_label(self.app.jobs.get(key,{})))

    def receipt_home(self):
        if not self.available():return
        self.clear_editor();self.current=None;self.show("queue","receipt",1)

    def back(self):
        if not self.available():return
        job=self.app.jobs.get(self.current) if self.current else None
        from file_rollback import has_files
        if job and self.section in ('receipt','outgoing') and has_files(job,self.section):
            self.pending_open=self.pending_register=None
            return self.app.send('file_rollback',{'hash':job['hash'],'scope':self.section,'step':self.step})
        if self.section=="receipt":
            if self.step==2:return self.receipt_home()
            if self.step==3 and job:
                if self.receipt_committed(job):return self.receipt_review(job)
                return self.title_step()
            if self.step==4 and job:return self.resume_receipt(job)
        elif self.section=="outgoing":
            if self.step==2:return self.outgoing_home()
            if self.step==3 and job:
                if job.get("outgoing",{}).get("sheet_attempted"):return self.outgoing_review(job)
                return self.start_outgoing(job)
            if self.step==4 and job:return self.outgoing_form(job)

    def rollback_finished(self,data):
        def ready():
            if not self.available():return self.app.root.after(50,ready)
            self.clear_editor()
            key=data['hash'];job=self.app.jobs[key]
            draft=self.drafts.get(key)
            if draft:
                for name in ('hwp_path','hwp_saved_number','hwp_created_sha256'):draft.pop(name,None)
                for name in ('folder_plan','save_folder','files_rolled_back'):
                    if name in job.get('outgoing',{}):draft[name]=job['outgoing'][name]
            self.current=key;self.section=data['scope'];self.step=data['step']
            self.focus(key)
            self.back()
        self.app.root.after(50,ready)

    def reconfigure(self):
        if not self.available():return
        job=self.app.jobs.get(self.current) if self.current else None
        if not job:
            if self.section=="receipt":return self.start_receipt()
            if self.section=="outgoing":return self.start_outgoing_selected()
            jobs=self.selected_jobs()
            if len(jobs)==1:return self.receipt_review(jobs[0])
            return messagebox.showinfo("문서 선택","확인할 문서를 한 개 선택하세요.")
        if self.section=="receipt" and self.step>1:
            return self.receipt_review(job) if self.receipt_committed(job) else self.title_step()
        if self.section=="outgoing" and self.step>1:
            return self.outgoing_review(job) if job.get("outgoing",{}).get("sheet_attempted") else self.start_outgoing(job)
        messagebox.showinfo("문서 선택","확인할 문서를 선택한 뒤 다음 단계로 진행하세요.")

    @staticmethod
    def receipt_committed(job):
        return bool(job.get("files_started") or job.get("sheet_attempted") or job.get("receipt_done") or job.get("state")=="완료")

    def receipt_review(self,job):
        self.clear_editor();self.current=job["hash"]
        page=self.empty("title");self.show("title","receipt",2)
        heading(page,"접수 제목과 첨부파일 확인","이미 저장·등록을 시작한 문서는 기존 선택 내용을 표시합니다. 이전 단계로 이동해도 접수번호와 이력은 유지됩니다.")
        ttk.Label(page,text=job["title"],font=("맑은 고딕",14,"bold"),wraplength=950).pack(anchor="w",pady=12)
        ttk.Label(page,text=" / ".join(str(job.get(k,"")) for k in ("category","receipt_type","date","recipient")),wraplength=950).pack(anchor="w",pady=8)
        box=tk.Text(page,height=8,wrap="word");box.pack(fill="both",expand=True,pady=12)
        box.insert("1.0","첨부파일\n"+"\n".join(str(f["path"]) for f in job.get("files",[])));box.configure(state="disabled")
        bar=ttk.Frame(page);bar.pack(fill="x",pady=8)
        ttk.Button(bar,text="이전: 첨부파일",command=self.receipt_home).pack(side="left")
        ttk.Button(bar,text="첨부파일 추가",command=self.add_attachments).pack(side="left",padx=8)
        ttk.Button(bar,text="다음: 저장 위치 확인",command=lambda:self.resume_receipt(self.app.jobs[job["hash"]])).pack(side="right")

    def outgoing_review(self,job):
        self.clear_editor();self.current=job["hash"]
        page=self.empty("outgoing_types");self.show("outgoing_types","outgoing",2)
        out=job.get("outgoing",{});key=out.get("template_id","")
        label=next((item["label"] for item in hwp_docs.catalog() if item["id"]==key),key or "선택 양식")
        heading(page,"등록에 사용한 발신 양식 확인","이미 등록을 시작한 발신은 기존 양식과 번호를 유지합니다. 다음 단계에서 작성 정보와 HWP를 확인할 수 있습니다.")
        ttk.Label(page,text=label,font=("맑은 고딕",15,"bold"),wraplength=950).pack(anchor="w",pady=12)
        ttk.Label(page,text=out.get("title",job["title"]),wraplength=950).pack(anchor="w",pady=10)
        ttk.Label(page,text="발신번호: "+str(out.get("number") or "등록 결과 확인 중"),font=("맑은 고딕",13,"bold")).pack(anchor="w",pady=10)
        bar=ttk.Frame(page);bar.pack(side="bottom",fill="x",pady=12)
        ttk.Button(bar,text="이전: 수신 문서",command=self.outgoing_home).pack(side="left")
        ttk.Button(bar,text="다음: 발신 정보",command=lambda:self.outgoing_form(self.app.jobs[job["hash"]])).pack(side="right")

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
        existing=[j for j in jobs if self.receipt_committed(j) or j.get('files_rolled_back')]
        if existing and len(jobs)>1:
            if len(existing)!=1:return messagebox.showinfo('첨부파일 추가','기존 공문은 한 건만 선택하고 새 첨부파일을 함께 선택하세요.')
            self.current=existing[0]['hash']
            return self.app.send('receipt_attach',{'hash':self.current,'jobs':[j['hash'] for j in jobs if j['hash']!=self.current]})
        if any(j.get("files_started") or j.get("sheet_attempted") for j in jobs):
            if len(jobs)>1:return messagebox.showinfo("재시도","이미 저장을 시작한 공문은 한 건씩 이어서 처리하세요.")
            return self.resume_receipt(jobs[0])
        self.title_step(jobs)

    def add_attachments(self):
        if not self.available():return
        jobs=self.selected_jobs()
        if len(jobs)!=1:return messagebox.showinfo('공문 선택','첨부파일을 추가할 기존 공문을 한 건 선택하세요.')
        from attachment_files import FILE_TYPES
        paths=filedialog.askopenfilenames(parent=self.app.root,title='이 공문에 추가할 첨부파일',initialdir=self.app.config.get('downloads',''),filetypes=FILE_TYPES)
        if paths:
            self.current=jobs[0]['hash']
            self.app.send('receipt_attach_paths',{'hash':self.current,'paths':list(paths)})

    def attachments_added(self,job):
        def ready():
            if not self.available():return self.app.root.after(50,ready)
            self.clear_editor();self.current=job['hash']
            self.focus(job['hash'])
            if self.receipt_committed(job):self.receipt_review(job)
            else:self.title_step([job])
        self.app.root.after(50,ready)

    def resume_receipt(self,job):
        if job.get('files_rolled_back'):return self.route_step()
        self.clear_editor();self.current=job["hash"]
        page=self.empty("route");self.show("route","receipt",3)
        done=bool(job.get("receipt_done") or job.get("state")=="완료")
        heading(page,"저장 위치와 접수 결과 확인" if done else "이전에 시작한 접수를 이어서 처리합니다",job.get("reason",""))
        ttk.Label(page,text=job["title"],wraplength=1000).pack(anchor="w",pady=12)
        ttk.Label(page,text="기존 제목·저장 위치·접수 결과를 확인합니다." if done else "이미 저장된 위치·제목·등록 표시를 기준으로 확인하고 재시도합니다.").pack(anchor="w")
        paths=job.get("destinations",[]) or [str(t.get("relative",""))+" / "+str(t.get("folder_name", "")) for t in job.get("routing",{}).get("targets",[])]
        ttk.Label(page,text="\n".join(map(str,paths)),wraplength=950).pack(anchor="w",pady=8)
        ttk.Button(page,text="저장 결과 확인",command=self.app.preview).pack(anchor="w",pady=12)
        ttk.Button(page,text="연결·설정 다시 확인",command=self.app.settings).pack(anchor="w",pady=6)
        if not done and not job.get("files_started") and not job.get("sheet_attempted"):
            ttk.Button(page,text="저장 위치 다시 선택",command=self.route_step).pack(anchor="w",pady=8)
        ttk.Button(page,text="이전: 제목 확인",command=self.back).pack(anchor="w",pady=8)
        if done:
            ttk.Button(page,text="다음: 접수 완료",command=lambda:self.completed(self.app.jobs[job["hash"]])).pack(anchor="e",pady=12)
        else:
            ttk.Button(page,text="접수 재시도",command=lambda:self.app.send("process",[self.app.jobs[job["hash"]]])).pack(anchor="e",pady=12)

    def recover(self):
        """A failed worker request must leave a usable screen, even if its panel already closed."""
        self.pending_register=None
        self.pending_open=None
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
        draft=self.receipt_drafts.get(self.current)
        if draft:
            editor=self.app.title_dialog
            editor.resume_title=draft["title"]
            editor.set_title(draft['title']);editor.relief_manual_title=draft.get('relief_manual_title',False)
            editor.file_box.current(max(0,min(draft["file"],len(editor.files)-1)))
        elif jobs[0].get("title_source"):
            self.app.title_dialog.resume_title=jobs[0]["title"]

    def route_step(self):
        self.clear_editor();page=self.empty("route");self.show("route","receipt",3)
        from route_chooser import RouteChooser
        job=self.app.jobs[self.current].copy()
        if self.current in self.route_drafts:job["routing"]=self.route_drafts[self.current]
        self.app.route_dialog=RouteChooser(self.app,job,parent=page)

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
        ttk.Button(bar,text='저장된 공문 폴더 열기',command=lambda:self.open_receipt_folders(job)).pack(side='left',padx=8)
        ttk.Button(bar,text="이 공문으로 발신 작성"+(" →" if self.continue_outgoing.get() else ""),command=lambda:self.start_outgoing(job)).pack(side="right")
        self.open_receipt_folders(job)

    def open_receipt_folders(self,job):
        opened=set();failed=[]
        for value in job.get('destinations',[]):
            folder=Path(value).parent
            key=os.path.normcase(os.path.abspath(folder))
            if key in opened:continue
            opened.add(key)
            try:os.startfile(str(folder))
            except OSError as error:failed.append(str(error))
        if failed:self.app.status.set('접수는 완료됐습니다. 폴더를 열지 못했습니다: '+failed[0])

    def start_outgoing_selected(self):
        jobs=self.selected_jobs()
        if len(jobs)!=1:return messagebox.showinfo("수신 문서 선택","발신에 사용할 수신 문서를 한 개 선택하세요.")
        self.start_outgoing(jobs[0])

    def start_new_outgoing(self):
        if not self.available():return
        self.app.send('outgoing_new',{})

    def outgoing_created(self,data):
        def ready():
            if not self.available():return self.app.root.after(50,ready)
            self.start_outgoing(self.app.jobs[data['hash']])
        self.app.root.after(50,ready)

    def start_outgoing(self,job):
        if not self.available():return
        self.clear_editor();self.focus(job["hash"])
        if job.get("outgoing",{}).get("sheet_attempted"):return self.outgoing_form(job)
        available=hwp_docs.catalog()
        if not available:
            messagebox.showinfo('HWP 양식 추가','사용할 양식이 없습니다. 원본 HWP 양식을 추가해 주세요.')
            from template_manager import open_pick_template
            return open_pick_template(self.app,on_added=lambda _:self.start_outgoing(job))
        try:recommended=hwp_docs.suggested_style(job)['id']
        except ValueError:recommended=available[0]['id']
        if job.get('standalone_outgoing') and any(item['id']=='1' for item in available):recommended='1'
        page=self.empty("outgoing_types");self.show("outgoing_types","outgoing",2)
        heading(page,"사용할 HWP 양식을 고르세요",job["title"] or '수신 없이 새로 보낼 공문의 양식을 선택하세요.')
        self.style_choice=tk.StringVar(value=self.drafts.get(job["hash"],{}).get("template_id") or job.get("outgoing",{}).get("template_id") or recommended)
        bar=ttk.Frame(page);bar.pack(fill="x",pady=16)
        bar.pack_configure(side="bottom")
        choices=ttk.PanedWindow(page,orient="horizontal");choices.pack(fill="both",expand=True)
        left,right=ttk.Frame(choices),ttk.Frame(choices,padding=12)
        choices.add(left,weight=2);choices.add(right,weight=1)
        picker=table(left,[("title","양식 이름",390),("family","종류",95)])
        preview=ttk.Label(right,text="양식을 선택하면 원본 미리보기를 표시합니다.",anchor="center",wraplength=280)
        preview.pack(fill="both",expand=True)
        detail=tk.StringVar();ttk.Label(right,textvariable=detail,wraplength=300).pack(fill="x",pady=8)
        def selected(_=None):
            keys=picker.selection()
            if not keys:return
            self.style_choice.set(keys[0]);item=hwp_docs.style(keys[0])
            from PIL import Image,ImageTk
            image=hwp_docs.template_preview(keys[0])
            if image is None:
                self.template_photo=None
                missing=not (hwp_docs.template_root()/item['file']).is_file()
                preview.configure(image='',text='원본 HWP가 연결되지 않았습니다.\n설정 · 연결 → 양식 관리 · 저장 위치에서\n이 양식의 HWP 연결을 눌러 원본을 지정하세요.' if missing else '이 양식에는 미리보기 이미지가 없습니다.\n선택 후 번호를 받은 사본을 한글에서 엽니다.')
            else:
                image.thumbnail((300,300),Image.Resampling.LANCZOS)
                self.template_photo=ImageTk.PhotoImage(image,master=page);image.close()
                preview.configure(image=self.template_photo,text="")
            detail.set(item["label"]+"\n"+item["family"]+" · "+item["folder"])
        picker.bind("<<TreeviewSelect>>",selected)
        def reload(item=None):
            for key in picker.get_children():picker.delete(key)
            for entry in hwp_docs.catalog():picker.insert("","end",iid=entry["id"],values=(entry["label"],entry["family"]))
            key=item["id"] if item else self.style_choice.get()
            if picker.exists(key):picker.selection_set(key);picker.see(key);selected()
        def add():
            from template_manager import open_pick_template
            open_pick_template(self.app,on_added=reload)
        reload()
        ttk.Button(bar,text="HWP 양식 넣기",command=add).pack(side="left",padx=8)
        ttk.Button(bar,text="이전: 발신 목록",command=self.outgoing_home).pack(side="left")
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
            self.app.commands.put(("process_outgoing",[self.app.jobs[key].copy()]))
            self.app.busy=True

    def outgoing_result(self,job):
        self.clear_editor();page=self.empty("outgoing_result");self.show("outgoing_result","outgoing",4)
        self.drafts.pop(job["hash"],None)
        out=job.get("outgoing",{});heading(page,"발신대장 · 직인날인대장 등록 완료",out.get("title",job["title"]))
        ttk.Label(page,text="발신번호",font=("맑은 고딕",12,"bold"),foreground="#667085").pack(anchor="w")
        ttk.Label(page,text=out.get("document_number") or out.get("number", ""),font=("맑은 고딕",30,"bold"),foreground="#1c5295").pack(anchor="w",pady=18)
        ttk.Label(page,text="직인 분류: "+out.get("ledger_type","공문")+" ○  ·  신청부서: "+out.get("stamp",{}).get("department","편의증진부")).pack(anchor="w")
        try:name=hwp_docs.filename(hwp_docs.style(out["template_id"]),out)
        except (ValueError,KeyError,StopIteration):name="양식과 저장 위치를 확인하세요."
        ttk.Label(page,text="HWP 저장 예정\n"+str(Path(out.get("save_folder", ""))/name),wraplength=1000).pack(anchor="w",pady=22)
        ttk.Label(page,text="한글을 열어 원본 양식의 기존 문단에서 수정하세요. Ctrl 스타일·들여쓰기·본문 서식을 유지합니다.",wraplength=1000).pack(anchor="w")
        ttk.Button(page,text="HWP 저장하고 한글 열기",command=lambda:self.export_registered(job)).pack(anchor="w",pady=16)
        sourcebar=ttk.Frame(page);sourcebar.pack(fill="x",pady=8)
        source=ttk.Combobox(sourcebar,values=[f"{i+1}. {Path(f['path']).name}" for i,f in enumerate(job["files"])],state="readonly")
        source.pack(side="left",fill="x",expand=True)
        main_hash=job.get("title_source",{}).get("file_hash",job["hash"])
        if job['files']:
            source.current(next((i for i,f in enumerate(job["files"]) if f["hash"]==main_hash),0))
            ttk.Button(sourcebar,text="정리된 본문 확인 · 복사",command=lambda:self.request_excerpt(job,job["files"][max(0,source.current())])).pack(side="right",padx=8)
        else:sourcebar.pack_forget()
        ttk.Button(page,text="발신 정보 · 저장 위치 확인",command=lambda:self.outgoing_form(self.app.jobs[job["hash"]])).pack(anchor="w",pady=6)
        ttk.Button(page,text="다른 문서 작성",command=self.outgoing_home).pack(anchor="e",side="bottom",pady=12)
        if self.pending_open==job["hash"]:
            self.pending_open=None
            def open_when_ready():
                if self.current!=job["hash"] or self.section!="outgoing" or self.step!=4:return
                if not self.available():return self.app.root.after(200,open_when_ready)
                self.export_registered(self.app.jobs[job["hash"]])
            self.app.root.after(200,open_when_ready)

    def request_excerpt(self,job,file):
        from attachment_files import can_extract
        if not can_extract(file['path']):return messagebox.showinfo('본문 파일 선택','본문을 읽을 PDF·HWP·HWPX 공문을 선택하세요. 다른 형식은 첨부파일로 보관합니다.')
        owner,request=uuid.uuid4().hex,uuid.uuid4().hex
        if self.app.send("draft_source",{"owner":owner,"request":request,"file":file}):
            self.excerpt_request=(owner,request,job["hash"])

    def deliver_excerpt(self,data):
        expected=self.excerpt_request
        if not expected or (data.get("owner"),data.get("request"))!=expected[:2]:return
        self.excerpt_request=None
        if self.current!=expected[2] or self.section!="outgoing":return
        from ocr_text_editor import OcrTextEditor
        OcrTextEditor(self.app,data["text"],parent=self.app.root,source_file=data['file'])

    def export_registered(self,job):
        # Use the ledger-authoritative saved metadata, never a suggested or sample number.
        out=job["outgoing"]
        existing=out.get("hwp_path","")
        if existing and str(out.get("hwp_saved_number",""))==str(out.get("number","")) and Path(existing).suffix.lower()==".hwp" and Path(existing).is_file():
            import os
            try:os.startfile(existing)
            except OSError as error:messagebox.showerror("한글 열기",str(error))
            return
        try:
            name=hwp_docs.filename(hwp_docs.style(out["template_id"]),out)
            folder=Path(out.get("save_folder",""))
            if not folder.is_absolute():raise ValueError("발신 정보에서 저장 폴더를 선택하세요.")
        except (ValueError,KeyError,StopIteration) as error:return messagebox.showerror("HWP 저장 확인",str(error))
        if (folder/name).is_file():
            import os
            try:os.startfile(str(folder/name))
            except OSError as error:messagebox.showerror("한글 열기",str(error))
            return
        self.app.send("draft_export",{"hash":job["hash"],"outgoing":out,"path":str(folder/name),"copy_only":False,"native_hwp":True})


class SimpleOutgoing(OutgoingEditor):
    """Keep the proven drafting methods; expose only fields needed for this document."""
    def __init__(self,app,job,parent,style_id=None):
        self.app,self.job,self.parent=app,job.copy(),app.root
        self.embedded=True;self.on_save=self.on_close=None
        self.owner,self.request=uuid.uuid4().hex,"";self.loading=False
        self.out=defaults(job,app.config);self.out["enabled"]=True
        from copy import deepcopy
        self.folder_plan=deepcopy(self.out.get("folder_plan"))
        self.folder_mode=self.out.get("folder_mode","")
        from outgoing_folders import receipt_case_folders,receipt_send_folders
        self.receipt_roots=receipt_case_folders(job,app.config)
        self.receipt_locations=receipt_send_folders(job,app.config)
        self.reuse_receipt_case=bool(self.receipt_roots) and not (self.out.get('hwp_path') or (self.folder_plan and (self.folder_plan.get('created') or self.folder_plan.get('folder_name'))))
        self.receipt_location=self.out.get('save_folder') if self.out.get('save_folder') in self.receipt_locations else next(iter(self.receipt_locations),'')
        if self.reuse_receipt_case:self.folder_plan=None;self.folder_mode='direct'
        self.changing_folder=False
        self.win=ttk.Frame(parent,padding=12);self.win.pack(fill="both",expand=True)
        actions=ttk.Frame(self.win);actions.pack(side="bottom",fill="x",pady=10)
        viewport=ttk.Frame(self.win);viewport.pack(fill="both",expand=True)
        canvas=tk.Canvas(viewport,highlightthickness=0,bg="#F3F6FB")
        scroll=ttk.Scrollbar(viewport,orient="vertical",command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")
        panel=ttk.Frame(canvas);window=canvas.create_window(0,0,anchor="nw",window=panel)
        panel.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(window,width=e.width))
        canvas.bind("<MouseWheel>",lambda e:canvas.yview_scroll(-int(e.delta/120)*3,"units"))
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
        if self.reuse_receipt_case:self.use_receipt_location()
        heading(panel,item["label"],"발신 제목과 수신 기관을 입력하세요. 선택한 양식을 한글에서 열어 본문을 작성합니다." if job.get('standalone_outgoing') else "수신 제목을 기본으로 가져옵니다. 필요한 항목만 수정하세요.")
        ttk.Label(panel,text="발신대장: "+self.family.get()+"  ·  직인 분류: "+item["family"]+" ○",foreground="#1c5295").pack(anchor="w",pady=(0,12))
        fields=ttk.Frame(panel);fields.pack(fill="x")
        self.controls=[]
        for row,(key,label) in enumerate((("title","발신 제목"),("receiver","수신 기관"),("date","발신일"),("sender","담당자"))):
            ttk.Label(fields,text=label).grid(row=row,column=0,sticky="w",pady=8)
            widget=ttk.Entry(fields,textvariable=self.vars[key]);widget.grid(row=row,column=1,sticky="ew",padx=12)
            self.controls.append(widget)
        fields.columnconfigure(1,weight=1)
        if not job.get('standalone_outgoing'):
            same=ttk.Checkbutton(panel,text="수신 제목과 동일",variable=self.same,command=self.use_receipt_title);same.pack(anchor="w",padx=80,pady=4);self.controls.append(same)
        ttk.Label(panel,text="저장 폴더").pack(anchor="w",pady=(12,2))
        route=ttk.Frame(panel);route.pack(fill="x")
        ttk.Entry(route,textvariable=self.vars["save_folder"]).pack(side="left",fill="x",expand=True)
        ttk.Button(route,text="변경",command=self.choose_destination).pack(side="right",padx=6)
        folderbar=ttk.Frame(panel);folderbar.pack(fill="x",pady=6)
        ttk.Button(folderbar,text="기존 저장 폴더 선택" if self.reuse_receipt_case else "폴더 이름 형식 · 내부 구성",command=self.configure_folders).pack(side="left")
        self.folder_summary=tk.StringVar(value=("수신 때 만든 센터발송 폴더에 HWP만 저장합니다." if self.receipt_locations else "수신 공문폴더 안에서 기존 발신 저장 폴더를 선택하세요.") if self.reuse_receipt_case else "기존 폴더 형식을 읽어 새 공문폴더와 내부폴더를 만들 수 있습니다.")
        ttk.Label(folderbar,textvariable=self.folder_summary,wraplength=650).pack(side="left",padx=10)
        def changed_folder(*_):
            if self.changing_folder:return
            if self.reuse_receipt_case:
                self.out['receipt_folder_override']=True;self.out['save_folder']=self.vars['save_folder'].get();self.folder_plan=None;self.folder_mode='direct'
                self.folder_summary.set('선택한 기존 폴더에 HWP만 저장합니다. 새 공문폴더를 만들지 않습니다.')
                return
            if self.folder_plan and not self.folder_plan.get("folder_name"):
                self.folder_plan=None;self.folder_mode=""
                self.folder_summary.set("저장 위치가 변경되었습니다. 기존 폴더 형식을 다시 확인합니다.")
        self.vars["save_folder"].trace_add("write",changed_folder)
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
        ttk.Button(panel,text="직인 신청정보 · 발신 방법",command=toggle_details).pack(anchor="w",pady=10)
        self.stamp_controls=[]
        for row,(key,label) in enumerate((("department","신청부서"),("applicant","신청자"),("person","날인자 (공란 가능)"),("date","날인일자"))):
            ttk.Label(detail,text=label).grid(row=row,column=0,sticky="w",pady=3)
            widget=ttk.Entry(detail,textvariable=self.stamp[key]);widget.grid(row=row,column=1,sticky="ew",padx=12);self.stamp_controls.append(widget)
        ttk.Label(detail,text="발신 방법").grid(row=4,column=0,sticky="w")
        method=ttk.Combobox(detail,textvariable=self.vars["method"],values=METHODS if item["family"]=="공문" else [x for x in METHODS if x!="문서24"],state="readonly");method.grid(row=4,column=1,sticky="ew",padx=12);self.controls.append(method)
        detail.columnconfigure(1,weight=1)
        source=ttk.Frame(panel);source.pack(fill="x",pady=8)
        self.file_var=tk.StringVar(value="1. "+Path(job["files"][0]["path"]).name if job['files'] else '')
        self.file_box=ttk.Combobox(source,textvariable=self.file_var,values=[f"{i+1}. {Path(f['path']).name}" for i,f in enumerate(job["files"])],state="readonly")
        self.file_box.pack(side="left",fill="x",expand=True);self.file_box.bind("<<ComboboxSelected>>",self.load_source)
        if job['files']:
            self.file_box.current(next((i for i,file in enumerate(job['files']) if file['hash']==job.get('text_file_hash',job['hash'])),0))
            ttk.Button(source,text="정리된 본문 확인 · 복사",command=self.prepare_excerpt).pack(side="right",padx=6)
        else:source.pack_forget()
        self.status=tk.StringVar(value="대장에서 실제 발신번호를 받은 뒤 원본 양식의 HWP를 저장하고 한글에서 편집합니다.")
        ttk.Label(panel,textvariable=self.status,wraplength=900).pack(anchor="w",pady=8)
        ttk.Button(actions,text="이전: 양식 선택",command=app.workflow.back).pack(side="left")
        ttk.Button(actions,text="작성 정보 저장",command=self.save).pack(side="left",padx=8)
        done=self.out.get("complete") and self.out.get("stamp",{}).get("complete")
        ttk.Button(actions,text="작성한 HWP 열기" if done else "발신번호 받고 한글로 열기",command=(lambda:app.workflow.export_registered(app.jobs[job["hash"]])) if done else self.register).pack(side="right")
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

    def use_receipt_location(self):
        if not self.reuse_receipt_case:return
        path=self.out.get('save_folder','') if self.out.get('receipt_folder_override') else self.receipt_location
        self.changing_folder=True;self.vars['save_folder'].set(path);self.changing_folder=False
        self.out['save_folder']=path
        self.folder_plan=None;self.folder_mode='direct'

    def choose_style(self,event=None):
        reuse=getattr(self,'reuse_receipt_case',False)
        if reuse:self.changing_folder=True
        try:super().choose_style(event)
        finally:
            if reuse:self.changing_folder=False
        if reuse:self.use_receipt_location()

    def register(self):
        if not self.app.workflow.available():return
        out=self.current()
        try:
            validate({**self.job,"outgoing":out},self.app.config)
            if not Path(out.get("save_folder","")).is_absolute():raise ValueError("HWP를 저장할 공유폴더를 선택하세요.")
            hwp_docs.check_template(out["template_id"])
        except (ValueError,OSError) as error:return messagebox.showerror("발신 정보 확인",str(error),parent=self.app.root)
        if not self.folder_plan and self.folder_mode!="direct":return self.configure_folders(resume=True)
        if self.folder_plan:
            try:
                from outgoing_folders import validate as validate_folder
                validate_folder(out["folder_plan"],out)
            except ValueError as error:return messagebox.showerror("폴더 구성 확인",str(error),parent=self.app.root)
        self.app.workflow.pending_register=self.job["hash"]
        self.app.workflow.pending_open=self.job["hash"]
        self.out["open_hwp_after_registration"]=True
        self.save()

    def current(self):
        out=super().current()
        if self.folder_plan:
            from copy import deepcopy
            plan=deepcopy(self.folder_plan)
            if not plan.get("folder_name"):
                plan["date"]=out["date"]
                if plan.get("title_same"):plan["folder_title"]=out["title"]
            out["folder_plan"]=plan;out["folder_mode"]="case"
        else:
            out["folder_plan"]=None;out["folder_mode"]=self.folder_mode
        return out

    def configure_folders(self,resume=False):
        if not self.app.workflow.available() or getattr(self.app,"outgoing_folder_dialog",None) is not None:return
        if self.reuse_receipt_case:
            path=filedialog.askdirectory(parent=self.app.root,title='수신 공문폴더 안의 기존 발신 저장 폴더 선택',initialdir=self.vars['save_folder'].get() or str(self.receipt_roots[0]),mustexist=True)
            if path:self.vars['save_folder'].set(path)
            if resume and path:self.app.root.after(100,self.register)
            return
        from outgoing_folder_chooser import OutgoingFolderChooser
        def applied(plan):
            self.folder_plan=plan;self.folder_mode="case" if plan else "direct"
            if plan:
                self.changing_folder=True
                self.vars["save_folder"].set(plan.get("save_path") or plan["base"])
                self.changing_folder=False
                self.folder_summary.set("공문폴더와 내부폴더 구성을 선택했습니다. 저장 시 최종 순번을 확정합니다.")
            else:self.folder_summary.set("공문폴더를 만들지 않고 HWP만 선택한 위치에 저장합니다.")
            if resume:self.app.root.after(100,self.register)
        OutgoingFolderChooser(self.app,self.job,self.current(),applied)

    def prepare_excerpt(self):
        if not self.app.workflow.available():return
        from attachment_files import can_extract
        if not can_extract(self.job['files'][max(0,self.file_box.current())]['path']):return messagebox.showinfo('본문 파일 선택','본문을 읽을 PDF·HWP·HWPX 공문을 선택하세요. 다른 형식은 첨부파일로 보관합니다.')
        text=self.source.get("1.0","end").strip()
        if not text:
            self.open_excerpt_when_loaded=True
            return self.load_source()
        from ocr_text_editor import OcrTextEditor
        OcrTextEditor(self.app,text,parent=self.app.root,source_file=self.job['files'][max(0,self.file_box.current())])

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
            self.app.workflow.pending_open=None
            return messagebox.showerror("날짜 확인","날짜를 YYYY-MM-DD 형식으로 입력하세요.")
        if not self.app.send("outgoing_save",{"hash":self.job["hash"],"outgoing":out}):
            self.app.workflow.pending_register=None
            self.app.workflow.pending_open=None

    def close(self,saved=False):
        self.win.destroy();self.app.outgoing_editor=None
