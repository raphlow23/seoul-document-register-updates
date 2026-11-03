"""Use sibling folder styles, with persistent fallbacks when a destination is changed."""
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox
import uuid
from folder_naming import AGENCIES,CHILD_KINDS,TEMPLATES,agency_for,preferred_template,folder_name,sequence_from_names,child_folders,infer_template


class OutgoingFolderChooser:
    def __init__(self,app,job,out,on_apply):
        self.app,self.job,self.out,self.on_apply=app,job,out,on_apply
        self.owner,self.request=uuid.uuid4().hex,uuid.uuid4().hex
        self.record=None;self.slot="outgoing:"+out["template_id"]
        plan=out.get("folder_plan") or {}
        saved=app.config.get("folder_profiles",{}).get(self.slot,app.config.get("folder_defaults",{}))
        self.base=plan.get("base") or out["save_folder"]
        self.agency=tk.StringVar(value=plan.get("agency") or agency_for(job.get("category","")) or saved.get("agency") or "서울시청 공문")
        self.title=tk.StringVar(value=plan.get("folder_title") or out["title"])
        self.template=tk.StringVar(value=plan.get("template") or "")
        self.manual_template=bool(plan.get('template_manual') or plan.get('folder_name') or plan.get('created'));self.changing_template=False
        children=plan.get("children",saved.get("children",app.config.get("folder_children",{}).get(self.agency.get(),CHILD_KINDS)))
        self.children={key:tk.BooleanVar(value=key in children) for key in CHILD_KINDS}
        self.save_in=tk.StringVar(value=plan.get("save_in",app.config.get("folder_profiles",{}).get(self.slot,{}).get("save_in","outgoing")))
        self.enabled=tk.BooleanVar(value=True)
        self.win=tk.Toplevel(app.root);self.win.title("발신 공문폴더 이름 · 내부 구성")
        self.win.geometry(f"970x{min(730,self.win.winfo_screenheight()-100)}");self.win.transient(app.root);self.win.grab_set();self.win.protocol("WM_DELETE_WINDOW",self.close)
        app.outgoing_folder_dialog=self
        outer=ttk.Frame(self.win,padding=16);outer.pack(fill="both",expand=True)
        footer=ttk.Frame(outer);footer.pack(side="bottom",fill="x",pady=10)
        ttk.Button(footer,text="취소",command=self.close).pack(side="left")
        self.apply_button=ttk.Button(footer,text="이 구성 사용",command=self.apply,state="disabled");self.apply_button.pack(side="right")
        ttk.Label(outer,text="기존 폴더 형식으로 새 공문폴더 만들기",font=("맑은 고딕",17,"bold")).pack(anchor="w")
        ttk.Label(outer,text=self.base,wraplength=910).pack(anchor="w",pady=8)
        ttk.Checkbutton(outer,text="공문폴더와 선택한 내부폴더 만들기 (해제하면 HWP만 바로 저장)",variable=self.enabled,command=self.redraw).pack(anchor="w",pady=4)
        top=ttk.Frame(outer);top.pack(fill="x",pady=6)
        ttk.Label(top,text="기관").pack(side="left")
        agency=ttk.Combobox(top,textvariable=self.agency,values=list(AGENCIES),state="readonly",width=20);agency.pack(side="left",padx=8);agency.bind("<<ComboboxSelected>>",self.redraw)
        self.names={};self.boxes={}
        for key in CHILD_KINDS:
            self.boxes[key]=ttk.Checkbutton(top,variable=self.children[key],command=self.redraw)
            self.boxes[key].pack(side="left",padx=8)
        middle=ttk.PanedWindow(outer,orient="horizontal");middle.pack(fill="both",expand=True,pady=8)
        left,right=ttk.Frame(middle),ttk.Frame(middle,padding=(15,0,0,0));middle.add(left,weight=1);middle.add(right,weight=2)
        ttk.Label(left,text="기존 폴더 이름 — 선택해서 같은 형식 사용").pack(anchor="w")
        self.examples=tk.Listbox(left,height=9,font=("맑은 고딕",10));self.examples.pack(fill="both",expand=True,pady=6);self.examples.bind("<<ListboxSelect>>",self.choose_example)
        ttk.Label(right,text="폴더에 사용할 제목 (발신대장·HWP 제목은 유지)").pack(anchor="w")
        ttk.Entry(right,textvariable=self.title).pack(fill="x",pady=6)
        ttk.Label(right,text="생성될 폴더 이름 — 기존 형식 자동 적용").pack(anchor="w")
        from folder_name_picker import ActualNamePicker
        self.name_picker=ActualNamePicker(right,self.template,self.title,lambda:self.record,lambda:self.out.get('date') or date.today().isoformat(),lambda:(self.out.get('folder_plan') or {}).get('folder_number'))
        self.name_picker.pack(fill='x',pady=6)
        location=ttk.Frame(right);location.pack(fill="x",pady=8)
        ttk.Label(location,text="HWP 넣을 위치").pack(side="left")
        self.location=ttk.Combobox(location,state="readonly",width=25);self.location.pack(side="left",padx=8)
        self.location.bind("<<ComboboxSelected>>",self.choose_location)
        self.preview=tk.Text(right,height=8,wrap="word",font=("맑은 고딕",10));self.preview.pack(fill="both",expand=True)
        self.status=tk.StringVar(value="선택한 저장 위치의 기존 폴더 이름을 읽는 중…")
        ttk.Label(outer,textvariable=self.status,wraplength=910).pack(fill="x",pady=6)
        for var in (self.title,self.template):var.trace_add("write",lambda *_:self.redraw())
        self.template.trace_add('write',self.template_changed)
        self.initial=self.win.after(100,self.load)

    def load(self):
        self.app.busy=True
        self.app.commands.put(("folder_describe",{"owner":self.owner,"request":self.request,"path":self.base}))

    def deliver(self,data):
        if data.get("owner")!=self.owner or data.get("request")!=self.request:return
        self.record=data["record"]
        for name in self.record["examples"]:self.examples.insert("end",name)
        if not self.manual_template:
            value=preferred_template(self.record,self.app.config,self.slot) if self.record['names'] else self.template.get() or preferred_template(self.record,self.app.config,self.slot)
            self.changing_template=True;self.template.set(value);self.changing_template=False
        self.status.set("기존 폴더와 같은 형식을 제안했습니다. 저장할 때 실제 다음 순번을 확정합니다." if self.record["names"] else "빈 저장 위치: 이전 이름 형식을 유지합니다. 번호 형식은 1부터, 날짜 형식은 날짜부터 시작합니다.")
        self.apply_button.configure(state="normal");self.redraw()

    def choose_example(self,_=None):
        selected=self.examples.curselection()
        if not selected:return
        template=infer_template(self.examples.get(selected[0]))
        if template:self.template.set(template)
        else:self.status.set("이 예시의 앞 형식을 확정하지 못했습니다. 위 이름 형식을 직접 수정해 주세요.")

    def template_changed(self,*_):
        if not self.changing_template:self.manual_template=True

    def choose_location(self,_=None):
        selected=self.location.current()
        if 0<=selected<len(self.names):self.save_in.set(list(self.names)[selected])
        self.redraw()

    def plan(self):
        old=self.out.get("folder_plan") or {}
        return {**old,"base":self.base,"template":self.template.get().strip(),'template_manual':self.manual_template,"folder_title":self.title.get().strip(),"title_same":self.title.get().strip()==self.out["title"],"agency":self.agency.get(),"children":[k for k,v in self.children.items() if v.get()],"save_in":self.save_in.get(),"date":self.out.get("date") or date.today().isoformat()}

    def redraw(self,_=None):
        if not hasattr(self,"preview"):return
        self.name_picker.refresh()
        labels=child_folders(self.agency.get(),CHILD_KINDS)
        for key,widget in self.boxes.items():widget.configure(text=labels[key].split(".",1)[1])
        children=child_folders(self.agency.get(),[k for k,v in self.children.items() if v.get()])
        self.names={"root":"상위 공문폴더",**children}
        if self.save_in.get() not in self.names:self.save_in.set("outgoing" if "outgoing" in self.names else "root")
        self.location.configure(values=list(self.names.values()));self.location.current(list(self.names).index(self.save_in.get()))
        if not self.enabled.get():text="HWP만 저장: "+self.base
        elif self.record:
            try:
                if not self.title.get().strip():raise ValueError("폴더 제목을 입력하세요.")
                plan=self.plan();ordinal=plan.get("folder_number") or sequence_from_names(self.record["names"],plan["template"])
                name=folder_name(plan["template"],plan["folder_title"],plan["date"],ordinal)
                text=name+"\n"+"\n".join("  └ "+value for value in children.values())+"\n\nHWP 저장: "+self.names[self.save_in.get()]
            except (ValueError,KeyError,TypeError) as error:text=str(error)
        else:text="기존 폴더를 읽고 있습니다."
        self.preview.configure(state="normal");self.preview.delete("1.0","end");self.preview.insert("1.0",text);self.preview.configure(state="disabled")

    def apply(self):
        if not self.record:return
        plan=self.plan() if self.enabled.get() else None
        try:
            if plan:
                if not plan["folder_title"]:raise ValueError("폴더 제목을 입력하세요.")
                from outgoing_folders import validate
                validate(plan,self.out)
        except ValueError as error:return messagebox.showerror("폴더 구성 확인",str(error),parent=self.win)
        self.on_apply(plan);self.close()

    def close(self):
        self.win.after_cancel(self.initial);self.win.destroy();self.app.outgoing_folder_dialog=None
