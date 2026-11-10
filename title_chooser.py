"""Choose a title source, click a suggestion, or OCR a rectangle in the PDF."""
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk, messagebox
import uuid


class TitleChooser:
    def __init__(self, app, jobs, parent=None):
        self.app, self.jobs = app, jobs
        self.owner = uuid.uuid4().hex
        self.request = ""
        self.primary = next((j for j in jobs if j.get("category")), jobs[0])
        self.files = []
        for job in jobs:
            for file in job["files"]:
                if file["hash"] not in [x["hash"] for x in self.files]:
                    self.files.append(file)
        from attachment_files import title_priority
        self.files.sort(key=title_priority)
        self.image = self.photo = None
        self.page, self.page_count = 0, 1
        self.zoom, self.scale = 1.0, 1.0
        self.selection = None
        self.mode = "manual"
        self.relief_auto_title='';self.relief_manual_title=False;self.category_trace=None
        self.agency = ""
        self.loaded = False
        self.embedded = parent is not None
        self.win = ttk.Frame(parent) if self.embedded else tk.Toplevel(app.root)
        if self.embedded:
            self.win.pack(fill="both", expand=True)
        else:
            self.win.title("첨부파일에서 수신접수 제목 고르기")
            self.win.geometry(f"{min(1320, self.win.winfo_screenwidth()-60)}x{min(900, self.win.winfo_screenheight()-100)}")
            self.win.minsize(950, 650)
            self.win.transient(app.root)
            self.win.grab_set()
            self.win.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(self.win, padding=6 if self.embedded else 14)
        outer.pack(fill="both", expand=True)
        if not self.embedded:
            ttk.Label(outer, text="제목을 가져올 첨부파일을 고르세요", font=("맑은 고딕", 16, "bold")).pack(anchor="w")
            ttk.Label(outer, text="제목 후보를 클릭하거나, PDF에서 원하는 글자 영역을 드래그해 읽을 수 있습니다. 여러 첨부는 한 공문으로 묶습니다.").pack(anchor="w", pady=(3, 10))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self.file_var = tk.StringVar(value=f"1. {Path(self.files[0]['path']).name}")
        self.file_box = ttk.Combobox(bar, textvariable=self.file_var, values=[f"{i+1}. {Path(f['path']).name}" for i,f in enumerate(self.files)], state="readonly", width=30)
        self.file_box.pack(side="left", fill="x", expand=True)
        self.file_box.bind("<<ComboboxSelected>>", self.change_file)
        ttk.Button(bar, text="이전 쪽", command=lambda: self.turn_page(-1)).pack(side="left", padx=4)
        ttk.Button(bar, text="다음 쪽", command=lambda: self.turn_page(1)).pack(side="left")
        self.page_label = tk.StringVar(value="1쪽")
        ttk.Label(bar, textvariable=self.page_label).pack(side="left", padx=7)
        panes = ttk.PanedWindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(5,0) if self.embedded else 10)
        left, right = ttk.Frame(panes), ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        tools = ttk.Frame(left)
        tools.pack(fill="x", pady=(0, 5))
        ttk.Button(tools, text="확대 +", command=lambda: self.change_zoom(1.25)).pack(side="left")
        ttk.Button(tools, text="축소 −", command=lambda: self.change_zoom(0.8)).pack(side="left", padx=3)
        ttk.Button(tools, text="90° 회전", command=self.rotate).pack(side="left")
        area = ttk.Frame(left)
        area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(area, bg="#e6eaf0", highlightthickness=0, width=500)
        vertical = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", self.drag_start)
        self.canvas.bind("<B1-Motion>", self.drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self.drag_end)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta/120)*3, "units"))
        cropbar = ttk.Frame(left)
        cropbar.pack(fill="x", pady=(6, 0))
        self.crop_mode = tk.StringVar(value="제목으로 바꾸기")
        ttk.Combobox(cropbar, textvariable=self.crop_mode, values=["제목으로 바꾸기", "뒤에 이어붙이기", "괄호로 붙이기"], state="readonly", width=18).pack(side="left")
        ttk.Button(cropbar, text="드래그한 영역 글자 읽기", command=self.read_region).pack(side="left", padx=6)
        right_top=ttk.Frame(right);right_top.pack(fill='x',pady=(0,1))
        ttk.Label(right_top,text="최종 수신접수 제목",font=("맑은 고딕",11,"bold")).pack(side='left')
        ttk.Button(right_top,text='본문 크게',command=self.expand_body).pack(side='right')
        ttk.Button(right_top,text='선택 → 제목',command=self.use_selection).pack(side='right',padx=3)
        title_area=ttk.Frame(right);title_area.pack(fill="x")
        self.title=tk.Text(title_area,height=2,width=42,wrap="word",font=("맑은 고딕",11),padx=6,pady=4)
        title_scroll=ttk.Scrollbar(title_area,command=self.title.yview);self.title.configure(yscrollcommand=title_scroll.set)
        self.title.pack(side="left",fill="x",expand=True);title_scroll.pack(side="right",fill="y")
        self.options_frame=ttk.Frame(bar);self.options_frame.pack(side='left',padx=(8,0))
        ttk.Label(self.options_frame,text="추천 제목").pack(side="left",padx=(0,5))
        self.choice=tk.IntVar(value=-1);self.title_options=[]
        self.option_box=ttk.Combobox(self.options_frame,state="readonly",width=14)
        self.option_box.pack(side="left",fill="x",expand=True)
        self.option_box.bind("<<ComboboxSelected>>",self.choose_option)
        self.remember=tk.BooleanVar(value=False)
        self.remember_button=ttk.Checkbutton(self.options_frame,text="형식 기억",variable=self.remember)
        self.remember_button.pack(side="right",padx=(5,0))
        self.body_area=ttk.Frame(right);self.body_area.pack(fill="both",expand=True)
        self.body=tk.Text(self.body_area,height=12,width=42,wrap="word",font=("맑은 고딕",11),padx=8,pady=6,exportselection=False)
        body_scroll=ttk.Scrollbar(self.body_area,command=self.body.yview);self.body.configure(yscrollcommand=body_scroll.set)
        self.body.pack(side="left",fill="both",expand=True);body_scroll.pack(side="right",fill="y")
        right.bind('<Configure>',lambda event:self.title.configure(height=2 if event.height<300 else 3))
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        self.status = tk.StringVar(value="첨부파일의 OCR 본문과 미리보기를 준비합니다.")
        ttk.Label(footer, textvariable=self.status, wraplength=900).pack(side="left", fill="x", expand=True)
        if self.embedded:
            ttk.Button(footer, text="이전: 첨부파일", command=app.workflow.receipt_home).pack(side="left", padx=6)
        self.apply_button = ttk.Button(footer, text="다음: 저장 위치" if self.embedded else "이 제목 사용", command=self.apply, state="disabled")
        self.apply_button.pack(side="right")
        if self.embedded:
            footer.pack_configure(side="bottom",before=panes,pady=(6,0))
            for widget in footer.winfo_children():
                if isinstance(widget,ttk.Label):widget.configure(wraplength=430)
        self.title.bind('<<Modified>>',self.title_modified)
        if self.embedded:
            self.category_var=self.app.fields['category']
            self.category_trace=self.category_var.trace_add('write',self.category_changed)
        else:self.category_var=tk.StringVar(value=self.primary.get('category',''))
        self.category_changed()
        self.initial_after = self.win.after(100, self.load)

    def is_relief(self):return self.category_var.get()=='완화공문'

    def title_modified(self,_=None):
        if self.title.edit_modified():
            if self.is_relief():self.relief_manual_title=True
            self.title.edit_modified(False)

    def category_changed(self,*_):
        if self.is_relief():
            if self.embedded:self.app.fields['receipt_type'].set('공문')
            self.relief_manual_title=False;self.set_relief_default(force=True)

    def set_relief_default(self,force=False,text=''):
        if not self.is_relief() or (self.relief_manual_title and not force):return
        from relief_intake import source_fields,receipt_title
        selected=self.selected_file()
        probe={**self.primary,'files':[selected,*[f for f in self.files if f!=selected]],'text':text or self.primary.get('text','')}
        fields=source_fields(probe);subject=fields['subject'] or '주소'
        self.relief_auto_title=receipt_title(subject,fields['suffix'])
        self.set_title(self.relief_auto_title)

    def selected_file(self):
        return self.files[max(0, self.file_box.current())]

    def enqueue(self, command, **payload):
        self.request = uuid.uuid4().hex
        self.loaded = False
        self.apply_button.configure(state="disabled")
        self.app.busy = True
        self.app.commands.put((command, {"owner": self.owner, "request": self.request, **payload}))

    def load(self):
        if not self.win.winfo_exists(): return
        self.status.set("PDF와 OCR 본문을 읽는 중…")
        self.enqueue("title_prepare", file=self.selected_file(), page=self.page)

    def change_file(self, _=None):
        self.page = 0
        self.load()

    def turn_page(self, delta):
        if self.loaded and 0 <= self.page+delta < self.page_count:
            self.page += delta
            self.load()

    def deliver(self, payload):
        if payload.get("request") != self.request or payload.get("owner") != self.owner:
            if payload.get("image") is not None: payload["image"].close()
            return
        self.loaded = True
        self.apply_button.configure(state="normal")
        if payload.get("kind") == "crop":
            text = re.sub(r"\s+", " ", payload["text"]).strip()
            old = self.title.get("1.0", "end").strip()
            mode = payload["crop_mode"]
            if text:
                self.set_title(text if mode == "제목으로 바꾸기" else old + (f"({text})" if mode == "괄호로 붙이기" else " " + text))
                self.mode = "region"
                self.remember.set(False)
                self.remember_button.configure(state="disabled")
            self.status.set(f"선택 영역 OCR {payload['confidence']:.1f}점 · 사업명·지번은 원본과 대조해 주세요.")
            return
        if self.image is not None: self.image.close()
        self.image = payload.get("image")
        self.page_count = payload.get("page_count", 1)
        self.page_label.set(f"{self.page+1}/{self.page_count}쪽")
        self.agency = payload.get("agency", "")
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", payload["text"])
        self.body.configure(state="disabled")
        options = payload["options"]
        self.title_options=options
        self.option_box.configure(values=[option['label'] for option in options])
        if options:self.option_box.current(0)
        else:self.option_box.set('추천 제목 없음')
        preserve=self.title.get('1.0','end').strip() if self.is_relief() and self.relief_manual_title else None
        if options:
            self.choice.set(0)
            self.choose(options[0],automatic=True)
        if hasattr(self,"resume_title"):
            self.set_title(self.resume_title);self.mode="manual";del self.resume_title
        if preserve is not None:self.set_title(preserve)
        elif self.is_relief():self.set_relief_default(text=payload['text'])
        self.draw()
        from attachment_files import can_extract
        self.status.set("다른 첨부파일을 고르거나 제목을 직접 입력하세요." if not can_extract(self.selected_file()['path']) else "원문을 드래그하거나 본문에서 문장을 선택하세요." if self.image else "본문에서 문장을 선택하거나 제목을 직접 입력하세요.")
        if payload.get('warning'):self.status.set(payload['warning'])

    def choose(self, option,automatic=False):
        self.set_title(option["title"])
        if self.is_relief() and not automatic:self.relief_manual_title=True
        self.mode = option["mode"]
        self.remember_button.configure(state="normal")

    def set_title(self, text):
        self.title.delete("1.0", "end")
        self.title.insert("1.0", text)
        self.title.edit_modified(False)

    def draw(self):
        from PIL import Image, ImageTk
        self.canvas.delete("all")
        self.selection = None
        if self.image is None: return
        self.scale = min(1.0, max(0.2, (self.canvas.winfo_width()-12)/self.image.width)) * self.zoom
        preview = self.image.resize((max(1, round(self.image.width*self.scale)), max(1, round(self.image.height*self.scale))), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(preview, master=self.win)
        preview.close()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.configure(scrollregion=(0,0,self.photo.width(),self.photo.height()))

    def change_zoom(self, multiplier):
        self.zoom = min(3, max(0.5, self.zoom*multiplier))
        self.draw()

    def rotate(self):
        if self.image is not None:
            turned = self.image.rotate(-90, expand=True)
            self.image.close()
            self.image = turned
            self.draw()

    def point(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def drag_start(self, event):
        if self.image is None or not self.loaded: return
        self.canvas.delete("selection")
        self.selection = [*self.point(event), *self.point(event)]

    def drag_motion(self, event):
        if not self.selection: return
        self.selection[2:] = self.point(event)
        self.canvas.delete("selection")
        self.canvas.create_rectangle(*self.selection, outline="#1769c2", width=2, tags="selection")

    def drag_end(self, event):
        self.drag_motion(event)

    def read_region(self):
        if not self.loaded or self.image is None or not self.selection: return
        x1,y1,x2,y2 = self.selection
        box = (max(0,int(min(x1,x2)/self.scale)), max(0,int(min(y1,y2)/self.scale)),
               min(self.image.width,int(max(x1,x2)/self.scale)), min(self.image.height,int(max(y1,y2)/self.scale)))
        if box[2]-box[0] < 15 or box[3]-box[1] < 10:
            return messagebox.showinfo("영역 선택", "읽을 글자가 들어오도록 영역을 조금 더 크게 선택해 주세요.", parent=self.win)
        self.status.set("선택한 영역을 한국어 OCR로 읽는 중…")
        self.enqueue("title_crop", image=self.image.crop(box), crop_mode=self.crop_mode.get())

    def choose_option(self,_=None):
        index=self.option_box.current()
        if 0<=index<len(self.title_options):
            self.choice.set(index);self.choose(self.title_options[index])

    def expand_body(self):
        existing=getattr(self,'body_window',None)
        if existing is not None and existing.winfo_exists():existing.lift();return existing
        window=tk.Toplevel(self.app.root);window.title('공문 OCR 본문 크게 보기')
        window.geometry(f'{min(1050,window.winfo_screenwidth()-60)}x{min(760,window.winfo_screenheight()-100)}');window.minsize(650,400)
        footer=ttk.Frame(window,padding=12);footer.pack(side='bottom',fill='x')
        area=ttk.Frame(window,padding=12);area.pack(fill='both',expand=True)
        text=tk.Text(area,wrap='word',font=('맑은 고딕',12),padx=12,pady=12,exportselection=False)
        scroll=ttk.Scrollbar(area,command=text.yview);text.configure(yscrollcommand=scroll.set)
        text.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        text.insert('1.0',self.body.get('1.0','end-1c'));text.configure(state='disabled')
        ttk.Button(footer,text='선택 문장을 제목으로',command=lambda:self.use_selection(text)).pack(side='right')
        ttk.Label(footer,text='문장을 선택해 제목에 넣거나 Ctrl+C로 복사하세요.',wraplength=350).pack(side='left',fill='x',expand=True,padx=(0,8))
        self.body_window=window;self.expanded_body=text
        return window

    def use_selection(self,source=None):
        source=source if source is not None else self.body
        try:
            self.set_title(re.sub(r"\s+", " ", source.get("sel.first", "sel.last")).strip())
            self.mode = "selection"
            self.remember.set(False)
            self.remember_button.configure(state="disabled")
        except tk.TclError:
            messagebox.showinfo("문장 선택", "오른쪽 본문에서 사용할 문장을 마우스로 먼저 선택하세요.", parent=self.win)

    def apply(self):
        title = self.title.get("1.0", "end").strip()
        if not self.loaded or not title:
            return messagebox.showinfo("제목 확인", "사용할 제목을 입력하거나 후보를 선택해 주세요.", parent=self.win)
        if len(title) > 500:
            return messagebox.showinfo("제목 확인", "제목을 500자 이내로 줄여 주세요.", parent=self.win)
        if self.is_relief() and title==self.relief_auto_title and title.endswith('(주소)'):
            return messagebox.showinfo('주소 확인','수신접수 제목의 괄호 안에 실제 주소를 입력해 주세요.',parent=self.win)
        if self.embedded:
            self.primary.update({k:v.get().strip() for k,v in self.app.fields.items() if k!="title"})
        self.app.busy = True
        self.app.commands.put(("title_apply", {"jobs": [j["hash"] for j in self.jobs], "primary": self.primary["hash"],
                                              "title": title, "source": {"file_hash": self.selected_file()["hash"], "page": self.page+1, "mode": self.mode},
                                              "fields": {k:self.primary.get(k,"공문" if k=="receipt_type" else "") for k in ("category", "date", "recipient", "receipt_type","issuer")},
                                              "agency": self.agency if self.remember.get() else ""}))
        self.close()

    def close(self):
        window=getattr(self,'body_window',None)
        if window is not None and window.winfo_exists():window.destroy()
        if self.category_trace:self.category_var.trace_remove('write',self.category_trace)
        self.win.after_cancel(self.initial_after)
        if self.image is not None:
            self.image.close()
            self.image = None
        self.win.destroy()
        self.app.title_dialog = None
