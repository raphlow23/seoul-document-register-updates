"""Choose agency, multiple destination branches and an existing folder naming style."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import uuid
from folder_naming import AGENCIES, TEMPLATES, agency_for, folder_name, sequence_from_names,child_folders,CHILD_KINDS,preferred_template


class RouteChooser:
    def __init__(self, app, job, parent=None):
        self.app, self.job = app, job
        self.owner, self.request = uuid.uuid4().hex, ""
        self.records, self.selected, self.templates = {}, set(), {}
        self.current, self.changing = None, False
        self.embedded = parent is not None
        self.win = ttk.Frame(parent) if self.embedded else tk.Toplevel(app.root)
        if self.embedded:
            self.win.pack(fill="both", expand=True)
        else:
            self.win.title("공문 종류와 저장 위치 선택")
            self.win.geometry(f"{min(1120,self.win.winfo_screenwidth()-60)}x{min(800,self.win.winfo_screenheight()-100)}")
            self.win.minsize(900, 630)
            self.win.transient(app.root)
            self.win.grab_set()
            self.win.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(self.win, padding=10 if self.embedded else 18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="공문 종류를 고르고 저장할 곳을 모두 체크하세요", font=("맑은 고딕", 16, "bold")).pack(anchor="w")
        title=job["title"] if not self.embedded else job["title"][:75]+("…" if len(job["title"])>75 else "")
        ttk.Label(outer, text=title, wraplength=950).pack(anchor="w", pady=(5, 8))
        self.agency = tk.StringVar(value=job.get("routing",{}).get("agency") or agency_for(job.get("category", "")))
        kind = ttk.Frame(outer)
        kind.pack(fill="x")
        for agency in AGENCIES:
            button = ttk.Radiobutton(kind, text=agency, variable=self.agency, value=agency, command=self.load)
            button.pack(side="left", padx=(0,20))
            if job.get("sheet_attempted"): button.configure(state="disabled")
        registration=ttk.Frame(outer);registration.pack(fill="x",pady=(8,0))
        ttk.Label(registration,text="수신 등록대장").pack(side="left")
        from core import RECEIPT_TABS
        self.receipt_type=tk.StringVar(value=job.get("receipt_type") or "공문")
        receipt=ttk.Combobox(registration,textvariable=self.receipt_type,values=list(RECEIPT_TABS),state="readonly",width=13)
        receipt.pack(side="left",padx=8)
        if job.get("sheet_attempted") or job.get("receipt_done"):receipt.configure(state="disabled")
        from outgoing import defaults
        self.outgoing=defaults(job,app.config)
        self.outgoing_enabled=tk.BooleanVar(value=self.outgoing["enabled"])
        outcheck=ttk.Checkbutton(registration,text="발신도 함께 등록 (직인대장 포함)",variable=self.outgoing_enabled,command=self.edit_outgoing)
        outcheck.pack(side="left",padx=10)
        if self.outgoing.get("sheet_attempted"):outcheck.configure(state="disabled")
        ttk.Button(registration,text="발신 정보·공문 작성",command=lambda:self.edit_outgoing(True)).pack(side="left")
        if self.embedded:
            for widget in registration.winfo_children()[2:]:widget.pack_forget()
            # Guided receipts commit only the receipt. Outgoing is reviewed in its own step.
            if not self.outgoing.get("sheet_attempted"):
                self.outgoing["enabled"]=False
                self.outgoing_enabled.set(False)
        panes = ttk.PanedWindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=12)
        left, right = ttk.Frame(panes), ttk.Frame(panes, padding=(14,0,0,0))
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        self.folder_title=tk.StringVar(value=job.get("routing",{}).get("folder_title") or job["title"])
        ttk.Label(right,text="폴더에 사용할 제목 (대장 제목은 유지)").pack(anchor="w")
        ttk.Entry(right,textvariable=self.folder_title).pack(fill="x",pady=(3,8))
        self.folder_title.trace_add("write",lambda *_:self.redraw_preview())
        ttk.Label(left, text="저장 위치 — 여러 곳 선택 가능", font=("맑은 고딕",11,"bold")).pack(anchor="w", pady=(0,6))
        self.children={key:tk.BooleanVar(value=key in job.get("routing",{}).get("children",[] if job.get("files_started") else app.config.get("folder_children",{}).get(self.agency.get(),app.config.get("folder_defaults",{}).get("children",CHILD_KINDS)))) for key in CHILD_KINDS}
        childbar=ttk.Frame(outer) if self.embedded else left
        if self.embedded:
            childbar.pack(fill="x",before=panes,pady=(6,0))
            ttk.Label(childbar,text="만들 내부폴더:").pack(side="left",padx=(0,10))
        for key,label in zip(CHILD_KINDS,("공문 (기관별 기초공문/중앙공문/시청공문)","센터발송","회신 (기관별 기초회신/중앙회신/시청회신)")):
            text={"incoming":"기관별 공문","outgoing":"센터발송","reply":"기관별 회신"}[key] if self.embedded else label
            ttk.Checkbutton(childbar,text=text,variable=self.children[key],command=self.redraw_preview).pack(side="left" if self.embedded else "top",anchor="w",padx=(0,10))
        area=ttk.Frame(left);area.pack(fill="both",expand=True)
        self.tree = ttk.Treeview(area, show="tree", selectmode="browse", height=12)
        scroll=ttk.Scrollbar(area,command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left",fill="both", expand=True);scroll.pack(side="right",fill="y")
        self.tree.bind("<ButtonRelease-1>", self.click)
        if not self.embedded:ttk.Label(left, text="체크된 각 위치 안에 새 공문 폴더를 만듭니다.\n원본 첨부파일은 다운로드 폴더에 남습니다.", wraplength=380).pack(anchor="w", pady=8)
        ttk.Label(right, text="기존 폴더 이름 예시", font=("맑은 고딕",11,"bold")).pack(anchor="w")
        self.examples = tk.Listbox(right, height=3 if self.embedded else 6, font=("맑은 고딕",10))
        self.examples.pack(fill="x", pady=7)
        self.examples.bind("<<ListboxSelect>>", self.select_example)
        ttk.Label(right, text="이름 형식 — 기존 예시를 클릭해 변경", wraplength=500).pack(anchor="w")
        self.template = tk.StringVar()
        self.template_box = ttk.Combobox(right, textvariable=self.template, values=TEMPLATES)
        self.template_box.pack(fill="x", pady=7)
        self.template.trace_add("write", self.changed)
        buttons = ttk.Frame(right)
        if not self.embedded:buttons.pack(fill="x")
        for label, template in (("날짜 + 제목",TEMPLATES[0]),("폴더순번 + 제목",TEMPLATES[4]),("제목만",TEMPLATES[-1])):
            ttk.Button(buttons, text=label, command=lambda t=template:self.template.set(t)).pack(side="left", padx=(0,5))
        if not self.embedded:ttk.Label(right, text="{title}=선택한 제목 · {date}=접수일 · {number:03d}=이 폴더의 다음 순번\n폴더 순번은 구글 시트 D열 접수번호와 별개입니다.", wraplength=580).pack(anchor="w", pady=8)
        ttk.Label(right, text="선택한 모든 위치의 생성 예정 이름", font=("맑은 고딕",11,"bold")).pack(anchor="w", pady=(8,3))
        self.preview = tk.Text(right, height=9, wrap="word", font=("맑은 고딕",10), padx=8,pady=8)
        self.preview.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="먼저 기초센터·중앙센터·시청 중 하나를 고르세요.")
        ttk.Label(outer, textvariable=self.status, wraplength=1050).pack(fill="x", pady=6)
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        if self.embedded:
            ttk.Button(footer, text="이전: 제목 확인", command=app.workflow.title_step).pack(side="left")
            ttk.Checkbutton(footer,text="접수 후 발신 공문도 이어서 작성",variable=app.workflow.continue_outgoing).pack(side="left",padx=12)
        else:
            ttk.Button(footer, text="선택만 적용", command=lambda:self.apply(False)).pack(side="right")
        ttk.Button(footer, text="저장하고 수신 접수", command=lambda:self.apply(True)).pack(side="right", padx=8)
        if self.embedded:footer.pack_configure(side="bottom",before=panes,pady=(6,0))
        self.initial_after = self.win.after(100, self.load) if self.agency.get() else None

    def load(self):
        if not self.agency.get(): return
        if not self.job.get("routing"):
            saved=self.app.config.get("folder_children",{}).get(self.agency.get())
            if saved is not None:
                for key,var in self.children.items():var.set(key in saved)
        self.request = uuid.uuid4().hex
        self.records, self.selected, self.templates, self.current = {}, set(), {}, None
        for node in self.tree.get_children(): self.tree.delete(node)
        self.status.set("공유폴더의 기존 하위 폴더 이름을 확인하는 중…")
        self.app.busy = True
        self.app.commands.put(("route_tree", {"owner":self.owner,"request":self.request,"agency":self.agency.get()}))

    def deliver(self, payload):
        if payload["owner"] != self.owner or payload["request"] != self.request: return
        old = {t["relative"]:t["template"] for t in self.job.get("routing",{}).get("targets",[])}
        for record in payload["records"]:
            key = record["relative"]
            self.records[key] = record
            self.tree.insert(record.get("parent", ""), "end", iid=key, text="☐ " + Path(key).name, open=True)
            self.templates[key] = old.get(key) or preferred_template(record,self.app.config,"incoming:"+key)
            if key in old:
                self.selected.add(key)
                self.tree.item(key, text="☑ " + Path(key).name)
        self.status.set("저장할 곳을 체크하고 기존 형식과 새 이름을 확인해 주세요. 폴더별 다음 순번을 사용하며 최종 순번은 저장 시 확정합니다.")
        self.redraw_preview()

    def click(self, event):
        key = self.tree.identify_row(event.y)
        if key not in self.records: return
        if key in self.selected:
            self.selected.remove(key)
        else:
            self.selected.add(key)
        self.tree.item(key, text=("☑ " if key in self.selected else "☐ ") + Path(key).name)
        self.current = key
        self.examples.delete(0,"end")
        for example in self.records[key]["examples"]: self.examples.insert("end", example)
        self.changing = True
        self.template.set(self.templates[key])
        self.template_box.configure(values=tuple(dict.fromkeys([s["template"] for s in self.records[key]["styles"]]+list(TEMPLATES))))
        self.changing = False
        self.redraw_preview()

    def select_example(self, _=None):
        from folder_naming import infer_template
        selected = self.examples.curselection()
        if not selected: return
        template = infer_template(self.examples.get(selected[0]))
        if template:
            self.template.set(template)
        else:
            self.status.set("이 예시의 앞 형식을 확정하지 못했습니다. 날짜·접수번호 버튼이나 형식 입력으로 직접 정해 주세요.")

    def changed(self, *_):
        if self.current and not self.changing:
            self.templates[self.current] = self.template.get()
        self.redraw_preview()

    def redraw_preview(self):
        lines = []
        for relative in sorted(self.selected):
            try:
                old = next((t for t in self.job.get("routing",{}).get("targets",[]) if t["relative"]==relative),{})
                ordinal = old.get("folder_number") or sequence_from_names(self.records[relative]["names"],self.templates.get(relative,""))
                name = folder_name(self.templates.get(relative,""),self.folder_title.get(),self.job["date"],ordinal)
            except (ValueError,TypeError,KeyError) as error:
                name = "이름 형식을 선택해 주세요: " + str(error)
            lines.append(relative+"\n  └ "+name)
            children=child_folders(self.agency.get(),[k for k,v in self.children.items() if v.get()])
            lines.extend("       └ "+child for child in children.values())
            lines.append("       첨부 저장: "+children.get("incoming","상위 공문 폴더"))
        self.preview.configure(state="normal")
        self.preview.delete("1.0","end")
        self.preview.insert("1.0","\n\n".join(lines) or "체크한 저장 위치가 여기에 표시됩니다.")
        self.preview.configure(state="disabled")

    def apply(self, execute):
        if not self.selected:
            return messagebox.showinfo("저장 위치", "저장할 곳을 한 곳 이상 체크해 주세요.",parent=self.win)
        targets = [{"relative":p,"template":self.templates[p]} for p in sorted(self.selected)]
        try:
            if not self.folder_title.get().strip():raise ValueError("폴더에 사용할 제목을 입력하세요.")
            for target in targets: folder_name(target["template"],self.folder_title.get(),self.job["date"],sequence_from_names(self.records[target["relative"]]["names"],target["template"]))
            if self.receipt_type.get() not in ("공문","업무연락","처리지침"):raise ValueError("수신 등록대장을 선택해 주세요.")
            self.outgoing["enabled"]=self.outgoing_enabled.get()
            if execute and self.outgoing["enabled"]:
                from outgoing import validate
                probe={**self.job,"outgoing":self.outgoing}
                self.outgoing=validate(probe,self.app.config)
        except (ValueError,TypeError,KeyError) as error:
            return messagebox.showerror("폴더 이름 확인",str(error),parent=self.win)
        self.app.busy = True
        self.app.commands.put(("route_apply",{"hash":self.job["hash"],"routing":{"agency":self.agency.get(),"targets":targets,"folder_title":self.folder_title.get().strip() if self.folder_title.get().strip()!=self.job["title"] else "","children":[k for k,v in self.children.items() if v.get()]},
                                             "fields":{**{k:self.job.get(k,"") for k in ("title","date","recipient","issuer")},"receipt_type":self.receipt_type.get()},
                                             "outgoing":self.outgoing,"execute":execute}))
        self.close()

    def edit_outgoing(self,force=False):
        if not force and not self.outgoing_enabled.get():return
        from outgoing_editor import OutgoingEditor
        def saved(out):
            self.outgoing=out
            self.outgoing_enabled.set(out["enabled"])
        def closed(was_saved):
            if not was_saved:self.outgoing_enabled.set(self.outgoing.get("enabled",False))
        OutgoingEditor(self.app,{**self.job,"category":self.agency.get() or self.job.get("category",""),"outgoing":self.outgoing},parent=self.win,on_save=saved,on_close=closed,enable=self.outgoing_enabled.get())

    def close(self):
        if self.initial_after: self.win.after_cancel(self.initial_after)
        self.win.destroy()
        self.app.route_dialog = None
