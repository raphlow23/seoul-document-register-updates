"""Choose agency, multiple destination branches and an existing folder naming style."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import uuid
import re
from folder_naming import AGENCIES, TEMPLATES, agency_for, folder_name, sequence_from_names,child_folders,CHILD_KINDS,ALL_CHILD_KINDS,child_kinds,preferred_template
import relief_intake as relief


class RouteChooser:
    def __init__(self, app, job, parent=None):
        self.app, self.job = app, job
        self.owner, self.request = uuid.uuid4().hex, ""
        self.records, self.selected, self.templates = {}, set(), {}
        self.manual_templates={t['relative'] for t in job.get('routing',{}).get('targets',[]) if t.get('template_manual')}
        self.current, self.changing = None, False
        self.child_sets={t['relative']:list(t['children']) for t in job.get('routing',{}).get('targets',[]) if 'children' in t}
        self.child_orders={t['relative']:list(t['child_order']) for t in job.get('routing',{}).get('targets',[]) if t.get('child_order') is not None}
        self.folder_titles={t['relative']:t.get('folder_title') or job.get('routing',{}).get('folder_title') or job['title'] for t in job.get('routing',{}).get('targets',[])}
        self.storage_modes={t['relative']:t.get('storage_mode','case') for t in job.get('routing',{}).get('targets',[])}
        self.year_folders={t['relative']:t.get('year_folder',job['date'][:4]) for t in job.get('routing',{}).get('targets',[])}
        self.relief_ready=bool(job.get('routing',{}).get('relief',{}).get('defaults_applied')) or bool(job.get('files_started'))
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
        self.receipt_label=tk.StringVar(value=title)
        ttk.Label(outer, textvariable=self.receipt_label, wraplength=950).pack(anchor="w", pady=(5, 8))
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
        self.location_label=tk.StringVar(value='설정할 저장 위치를 선택하세요')
        ttk.Label(right,textvariable=self.location_label,foreground='#1c5295',wraplength=550).pack(anchor='w')
        self.relief_panel=ttk.LabelFrame(right,text='완화신청 기본 제목',padding=5)
        initial={**relief.source_fields(job),**job.get('routing',{}).get('relief',{})}
        self.relief_vars={k:tk.StringVar(value=initial.get(k,'')) for k in ('district','subject','suffix')}
        self.relief_vars['receipt_title']=tk.StringVar(value=initial.get('receipt_title') or relief.receipt_title(initial['subject'],initial['suffix']))
        self.relief_auto_receipt=relief.receipt_title(initial['subject'],initial['suffix'])
        self.relief_auto_folder=relief.folder_title(initial['district'],initial['subject'],initial['suffix'])
        for key,label,row,col,span,width in (('district','구청명',0,0,1,12),('subject','주소·사업명',0,2,1,25),('receipt_title','수신접수 제목',1,0,3,45)):
            ttk.Label(self.relief_panel,text=label).grid(row=row,column=col,sticky='w',padx=(0,5))
            entry=ttk.Entry(self.relief_panel,textvariable=self.relief_vars[key],width=width);entry.grid(row=row,column=col+1,columnspan=span,sticky='ew',pady=2)
            if job.get('files_started') or job.get('sheet_attempted') or job.get('receipt_done'):entry.configure(state='readonly')
        self.relief_panel.columnconfigure(3,weight=1)
        for key in ('district','subject'):self.relief_vars[key].trace_add('write',self.relief_changed)
        self.relief_vars['receipt_title'].trace_add('write',lambda *_:self.redraw_preview() if not self.changing else None)
        ttk.Label(right,text="선택한 위치의 폴더 제목 (대장 제목은 유지)").pack(anchor="w")
        ttk.Entry(right,textvariable=self.folder_title).pack(fill="x",pady=(3,8))
        self.folder_title.trace_add("write",self.title_changed)
        options=ttk.Frame(right);options.pack(fill='x',pady=(0,6))
        self.make_children=tk.BooleanVar(value=True)
        ttk.Checkbutton(options,text='이 위치에 내부 폴더 만들기',variable=self.make_children,command=self.toggle_children).pack(anchor='w')
        self.year_only=tk.BooleanVar(value=False)
        self.year_check=ttk.Checkbutton(options,text='지침: 연도 폴더에 수신 파일만 저장 (공문 폴더 생략)',variable=self.year_only,command=self.mode_changed)
        self.year_check.pack(anchor='w')
        self.year_folder=tk.StringVar(value=job['date'][:4])
        self.year_box=ttk.Combobox(options,textvariable=self.year_folder,width=12,state='disabled')
        self.year_box.pack(anchor='w');self.year_folder.trace_add('write',self.year_changed)
        ttk.Label(left, text="저장 위치 — 여러 곳 선택 가능", font=("맑은 고딕",11,"bold")).pack(anchor="w", pady=(0,6))
        self.children={key:tk.BooleanVar(value=key in job.get("routing",{}).get("children",[] if job.get("files_started") else app.config.get("folder_children",{}).get(self.agency.get(),app.config.get("folder_defaults",{}).get("children",CHILD_KINDS)))) for key in ALL_CHILD_KINDS}
        childbar=ttk.Frame(outer) if self.embedded else left
        self.childbar=childbar
        if self.embedded:
            childbar.pack(fill="x",before=panes,pady=(6,0))
            ttk.Label(childbar,text="만들 내부폴더:").pack(side="left",padx=(0,10))
        self.child_boxes={}
        for key in ALL_CHILD_KINDS:
            text={'incoming':'기관별 공문','outgoing':'센터발송','drawings':'협의도면','photos':'현장사진','reply':'기관별 회신'}[key]
            box=ttk.Checkbutton(childbar,text=text,variable=self.children[key],command=self.children_changed)
            self.child_boxes[key]=box
            if key in CHILD_KINDS:box.pack(side='left' if self.embedded else 'top',anchor='w',padx=(0,10))
        self.child_edit_button=ttk.Button(childbar,text='폴더 추가 · 순서 변경',command=self.edit_children)
        self.child_edit_button.pack(side='left' if self.embedded else 'top',padx=5)
        area=ttk.Frame(left);area.pack(fill="both",expand=True)
        self.tree = ttk.Treeview(area, show="tree", selectmode="browse", height=12)
        scroll=ttk.Scrollbar(area,command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left",fill="both", expand=True);scroll.pack(side="right",fill="y")
        self.tree.bind("<ButtonRelease-1>", self.click)
        self.tree.bind('<<TreeviewSelect>>',lambda _:(self.focus_folder(self.tree.selection()[0]),self.redraw_preview()) if self.tree.selection() and self.tree.selection()[0] in self.records else None)
        ttk.Label(left,text='왼쪽 체크: 저장 여부 · 위치 이름: 해당 위치 설정',wraplength=380).pack(anchor='w')
        if not self.embedded:ttk.Label(left, text="체크된 각 위치 안에 새 공문 폴더를 만듭니다.\n원본 첨부파일은 다운로드 폴더에 남습니다.", wraplength=380).pack(anchor="w", pady=8)
        ttk.Label(right, text="기존 폴더 이름 예시", font=("맑은 고딕",11,"bold")).pack(anchor="w")
        self.examples = tk.Listbox(right, height=3 if self.embedded else 6, font=("맑은 고딕",10))
        self.examples.pack(fill="x", pady=7)
        self.examples.bind("<<ListboxSelect>>", self.select_example)
        ttk.Label(right, text="생성될 폴더 이름 — 기존 형식 자동 적용", wraplength=500).pack(anchor="w")
        self.template = tk.StringVar()
        from folder_name_picker import ActualNamePicker
        self.name_picker=ActualNamePicker(right,self.template,self.folder_title,lambda:self.records.get(self.current),lambda:self.job['date'],lambda:next((t.get('folder_number') for t in self.job.get('routing',{}).get('targets',[]) if t['relative']==self.current),None))
        self.name_picker.pack(fill='x',pady=7);self.template_box=self.name_picker.box
        self.template.trace_add("write", self.changed)
        buttons = ttk.Frame(right)
        if not self.embedded:buttons.pack(fill="x")
        for label, template in (("날짜 + 제목",TEMPLATES[0]),("폴더순번 + 제목",TEMPLATES[4]),("제목만",TEMPLATES[-1])):
            ttk.Button(buttons, text=label, command=lambda t=template:self.template.set(t)).pack(side="left", padx=(0,5))
        if not self.embedded:ttk.Label(right, text="번호·날짜는 기존 폴더를 기준으로 계산합니다.\n폴더 순번은 구글 시트 접수번호와 별개입니다.", wraplength=580).pack(anchor="w", pady=8)
        ttk.Label(right, text="선택한 모든 위치의 생성 예정 이름", font=("맑은 고딕",11,"bold")).pack(anchor="w", pady=(8,3))
        self.preview = tk.Text(right, height=9, wrap="word", font=("맑은 고딕",10), padx=8,pady=8)
        self.preview.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="먼저 기초센터·중앙센터·시청 중 하나를 고르세요.")
        ttk.Label(outer, textvariable=self.status, wraplength=1050).pack(fill="x", pady=6)
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        if self.embedded:
            ttk.Button(footer, text="이전: 제목 확인", command=app.workflow.back).pack(side="left")
            ttk.Button(footer,text="첨부파일 추가",command=app.workflow.add_attachments).pack(side="left",padx=5)
            ttk.Checkbutton(footer,text="접수 후 발신 공문도 이어서 작성",variable=app.workflow.continue_outgoing).pack(side="left",padx=12)
        else:
            ttk.Button(footer, text="선택만 적용", command=lambda:self.apply(False)).pack(side="right")
        ttk.Button(footer, text="파일만 다시 저장 (접수번호 유지)" if job.get('receipt_done') and job.get('files_rolled_back') else "저장하고 수신 접수", command=lambda:self.apply(True)).pack(side="right", padx=8)
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
            if key not in self.child_sets:
                kinds=child_kinds(key)
                saved=self.app.config.get('folder_profiles',{}).get('incoming:'+key,{})
                self.storage_modes.setdefault(key,saved.get('storage_mode','case'))
                if saved.get('year_folder'):self.year_folders.setdefault(key,saved['year_folder'])
                self.child_sets[key]=list(saved.get('children',kinds) if kinds!=CHILD_KINDS else saved.get('children',self.job.get('routing',{}).get('children',[k for k,v in self.children.items() if v.get() and k in kinds])))
                if saved.get('child_order') is not None and not self.job.get('files_started'):self.child_orders[key]=list(saved['child_order'])
            self.tree.insert(record.get("parent", ""), "end", iid=key, text="☐ " + Path(key).name, open=True)
            self.templates[key] = old.get(key) if key in old and (key in self.manual_templates or self.job.get('files_started') or not record['names']) else preferred_template(record,self.app.config,"incoming:"+key)
            if key in old:
                self.selected.add(key)
                self.tree.item(key, text="☑ " + Path(key).name)
        self.status.set("저장할 곳을 체크하고 기존 형식과 새 이름을 확인해 주세요. 폴더별 다음 순번을 사용하며 최종 순번은 저장 시 확정합니다.")
        if self.records:self.focus_folder(next(iter(sorted(self.selected)),next(iter(self.records))))
        self.redraw_preview()

    def click(self, event):
        key = self.tree.identify_row(event.y)
        if key not in self.records: return
        if 'indicator' in self.tree.identify_element(event.x,event.y):return
        # Clicking a name changes the editing destination; only the checkbox toggles it.
        box_left=self.tree.bbox(key)[0]+(20 if self.records[key].get('parent') else 0)
        if event.x>box_left+36:
            self.focus_folder(key);self.redraw_preview();return
        if key in self.selected:
            self.selected.remove(key)
        else:
            self.selected.add(key)
        self.tree.item(key, text=("☑ " if key in self.selected else "☐ ") + Path(key).name)
        self.focus_folder(key)
        self.redraw_preview()

    def focus_folder(self,key):
        self.current=key
        self.changing=True
        if key==relief.LOCATION:
            self.relief_panel.pack(fill='x',after=self.relief_panel.master.winfo_children()[0],pady=(3,6))
            if not self.relief_ready:
                self.folder_titles[key]=relief.folder_title(*(self.relief_vars[k].get() for k in ('district','subject','suffix')))
                if key not in self.manual_templates:self.templates[key]=relief.default_template(self.templates[key])
                self.relief_ready=True
        else:self.relief_panel.pack_forget()
        self.location_label.set('현재 설정: '+key)
        self.folder_title.set(self.folder_titles.get(key,self.job.get('routing',{}).get('folder_title') or self.job['title']))
        self.folder_titles[key]=self.folder_title.get()
        guideline=self.is_guideline(key)
        if guideline:self.year_check.pack(anchor='w')
        else:self.year_check.pack_forget()
        self.year_check.configure(state='normal' if guideline else 'disabled')
        self.year_only.set(self.storage_modes.get(key,'case')=='year')
        self.year_folder.set(self.year_folders.get(key,self.job['date'][:4]))
        years=[n for n in self.records[key]['names'] if re.fullmatch(r'20\d{2}년?',n)]
        if key not in self.year_folders:
            self.year_folder.set(next((n for n in years if n.rstrip('년')==self.job['date'][:4]),self.job['date'][:4]))
            self.year_folders[key]=self.year_folder.get()
        self.year_box.configure(values=years or [self.job['date'][:4]],state='normal' if self.year_only.get() else 'disabled')
        if self.year_only.get():self.year_box.pack(anchor='w')
        else:self.year_box.pack_forget()
        self.make_children.set(bool(self.child_sets.get(key,[])) and not self.year_only.get())
        kinds=self.child_sequence(key)
        for box in self.child_boxes.values():box.pack_forget()
        self.child_edit_button.pack_forget()
        labels=child_folders(self.agency.get(),kinds,kinds)
        for child in kinds:
            if child not in self.children:
                self.children[child]=tk.BooleanVar()
                self.child_boxes[child]=ttk.Checkbutton(self.childbar,variable=self.children[child],command=self.children_changed)
        for child,var in self.children.items():var.set(child in self.child_sets.get(key,kinds))
        for child in kinds:
            self.child_boxes[child].configure(text=labels[child].split('.',1)[1])
            if not child.startswith('custom:'):self.child_boxes[child].pack(side='left' if self.embedded else 'top',anchor='w',padx=(0,10))
        custom_count=sum(k.startswith('custom:') for k in kinds)
        self.child_edit_button.configure(text='폴더 추가 · 순서 변경'+(f' (+{custom_count})' if custom_count else ''))
        self.child_edit_button.pack(side='left' if self.embedded else 'top',padx=5)
        self.examples.delete(0,"end")
        for example in self.records[key]["examples"]: self.examples.insert("end", example)
        self.changing = True
        self.template.set(self.templates[key])
        self.changing = False
        self.update_child_state()
        self.name_picker.refresh()

    def relief_values(self):
        return {**{k:v.get().strip() for k,v in self.relief_vars.items()},'defaults_applied':self.relief_ready}

    def registration_title(self):
        if relief.LOCATION in self.selected and not (self.job.get('files_started') or self.job.get('sheet_attempted') or self.job.get('receipt_done')):return self.relief_vars['receipt_title'].get().strip()
        return self.job['title']

    def relief_changed(self,*_):
        if self.changing or self.job.get('files_started') or self.job.get('sheet_attempted'):return
        values=self.relief_values()
        self.changing=True
        new_receipt=relief.receipt_title(values['subject'],values['suffix'])
        new_folder=relief.folder_title(values['district'],values['subject'],values['suffix'])
        if values['receipt_title']==self.relief_auto_receipt:self.relief_vars['receipt_title'].set(new_receipt)
        if self.relief_ready:
            if self.folder_titles.get(relief.LOCATION)==self.relief_auto_folder:self.folder_titles[relief.LOCATION]=new_folder
            if self.current==relief.LOCATION:self.folder_title.set(self.folder_titles[relief.LOCATION])
        self.relief_auto_receipt,self.relief_auto_folder=new_receipt,new_folder
        self.changing=False
        self.redraw_preview()

    def is_guideline(self,key):
        return '5.보건복지부 및 중앙처리지침' in key and ('중앙지침' in key or '보건복지부 지침' in key)

    def title_changed(self,*_):
        if self.current and not self.changing:self.folder_titles[self.current]=self.folder_title.get()
        if not self.changing:self.redraw_preview()

    def year_changed(self,*_):
        if self.current and not self.changing:
            self.year_folders[self.current]=self.year_folder.get();self.redraw_preview()

    def mode_changed(self):
        if not self.current:return
        self.storage_modes[self.current]='year' if self.year_only.get() else 'case'
        if self.year_only.get():
            self.make_children.set(False);self.child_sets[self.current]=[]
            for var in self.children.values():var.set(False)
        self.year_box.configure(state='normal' if self.year_only.get() else 'disabled')
        if self.year_only.get():self.year_box.pack(anchor='w')
        else:self.year_box.pack_forget()
        self.update_child_state();self.redraw_preview()

    def update_child_state(self):
        state='normal' if self.make_children.get() and not self.year_only.get() else 'disabled'
        for box in self.child_boxes.values():box.configure(state=state)
        self.child_edit_button.configure(state=state)

    def toggle_children(self):
        if not self.current:return
        if self.make_children.get():
            self.year_only.set(False);self.storage_modes[self.current]='case'
            self.child_sets[self.current]=list(self.child_sequence(self.current))
        else:self.child_sets[self.current]=[]
        self.focus_folder(self.current);self.redraw_preview()

    def targets(self):
        return [{"relative":p,"template":self.templates[p],'template_manual':p in self.manual_templates,
                 'folder_title':self.folder_titles.get(p,self.job['title']),'children':self.child_sets.get(p,[]),
                 'storage_mode':self.storage_modes.get(p,'case'),'year_folder':self.year_folders.get(p,self.job['date'][:4]),
                 **({'child_order':self.child_orders[p]} if p in self.child_orders else {})} for p in sorted(self.selected)]

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
            self.manual_templates.add(self.current)
        self.redraw_preview()

    def children_changed(self):
        if self.current:self.child_sets[self.current]=[k for k in self.child_sequence(self.current) if self.children[k].get()]
        if self.current:self.make_children.set(bool(self.child_sets[self.current]));self.update_child_state()
        self.redraw_preview()

    def child_sequence(self,key):
        return self.child_orders.get(key) or [*child_kinds(key),*[k for k in self.child_sets.get(key,[]) if k.startswith('custom:')]]

    def edit_children(self):
        if not self.current:return
        from child_folder_editor import ChildFolderEditor
        key=self.current
        def applied(order,selected):
            self.child_orders[key]=order;self.child_sets[key]=selected
            self.focus_folder(key);self.redraw_preview()
        ChildFolderEditor(self.win,self.agency.get(),self.child_sequence(key),self.child_sets.get(key,[]),applied)

    def redraw_preview(self):
        self.receipt_label.set(self.registration_title())
        if self.current:self.child_sets[self.current]=[k for k in self.child_sequence(self.current) if self.children[k].get()]
        if hasattr(self,'name_picker'):self.name_picker.refresh()
        if self.current and hasattr(self,'name_picker'):
            year=self.storage_modes.get(self.current)=='year'
            for widget in self.name_picker.winfo_children():widget.configure(state='disabled' if year else 'readonly' if widget is self.name_picker.box else 'normal')
            if year:self.name_picker.value.set(self.year_folders.get(self.current,self.job['date'][:4])+' (수신 파일만 저장)')
        lines = []
        for relative in sorted(self.selected):
            try:
                old = next((t for t in self.job.get("routing",{}).get("targets",[]) if t["relative"]==relative),{})
                ordinal = old.get("folder_number") or sequence_from_names(self.records[relative]["names"],self.templates.get(relative,""))
                name = self.year_folders.get(relative,self.job['date'][:4]) if self.storage_modes.get(relative)=='year' else folder_name(self.templates.get(relative,""),self.folder_titles.get(relative,self.job['title']),self.job["date"],ordinal)
            except (ValueError,TypeError,KeyError) as error:
                name = "이름 형식을 선택해 주세요: " + str(error)
            lines.append(relative+"\n  └ "+name)
            children=child_folders(self.agency.get(),self.child_sets.get(relative,CHILD_KINDS),self.child_orders.get(relative))
            lines.extend("       └ "+child for child in children.values())
            lines.append("       첨부 저장: "+children.get("incoming","상위 공문 폴더"))
        self.preview.configure(state="normal")
        self.preview.delete("1.0","end")
        self.preview.insert("1.0","\n\n".join(lines) or "체크한 저장 위치가 여기에 표시됩니다.")
        self.preview.configure(state="disabled")

    def apply(self, execute):
        if not self.selected:
            return messagebox.showinfo("저장 위치", "저장할 곳을 한 곳 이상 체크해 주세요.",parent=self.win)
        self.children_changed()
        targets = self.targets()
        try:
            if relief.LOCATION in self.selected and not (self.job.get('files_started') or self.job.get('sheet_attempted') or self.job.get('receipt_done')):relief.validate(self.relief_values())
            if not self.folder_title.get().strip():raise ValueError("폴더에 사용할 제목을 입력하세요.")
            from folder_naming import validate_target_mode
            for target in targets:
                validate_target_mode(target)
                if target.get('storage_mode')!='year':folder_name(target["template"],target['folder_title'],self.job["date"],sequence_from_names(self.records[target["relative"]]["names"],target["template"]))
            if self.receipt_type.get() not in ("공문","업무연락","처리지침"):raise ValueError("수신 등록대장을 선택해 주세요.")
            self.outgoing["enabled"]=self.outgoing_enabled.get()
            if self.outgoing.get('title')==self.job['title']:self.outgoing['title']=self.registration_title()
            if execute and self.outgoing["enabled"]:
                from outgoing import validate
                probe={**self.job,"outgoing":self.outgoing}
                self.outgoing=validate(probe,self.app.config)
        except (ValueError,TypeError,KeyError) as error:
            return messagebox.showerror("폴더 이름 확인",str(error),parent=self.win)
        self.app.busy = True
        self.app.commands.put(("route_apply",{"hash":self.job["hash"],"routing":{"agency":self.agency.get(),"targets":targets,"folder_title":"","children":[],"relief":self.relief_values()},
                                             "fields":{**{k:self.job.get(k,"") for k in ("date","recipient","issuer")},"title":self.registration_title(),"receipt_type":self.receipt_type.get()},
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
