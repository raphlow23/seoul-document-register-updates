"""Review an OCR excerpt and spacing changes before copying or inserting into a draft."""
import tkinter as tk
from tkinter import ttk,messagebox
import uuid


class OcrTextEditor:
    def __init__(self,app,text,parent=None,selected=False,on_insert=None,on_hwp=None,source_file=None):
        self.app,self.parent=app,parent or app.root
        self.owner=uuid.uuid4().hex;self.request="";self.on_insert=on_insert;self.on_hwp=on_hwp
        self.source_file=source_file;self.preview_request='';self.ready=False
        self.win=tk.Toplevel(self.parent);self.win.title("공문 원본 확인 · 자동 정리된 본문 수정·복사")
        self.win.geometry("1120x730");self.win.transient(self.parent);self.win.grab_set();self.win.protocol("WM_DELETE_WINDOW",self.close)
        app.ocr_text_editor=self
        box=ttk.Frame(self.win,padding=14);box.pack(fill="both",expand=True)
        bar=ttk.Frame(box);bar.pack(fill="x")
        self.mode=tk.StringVar(value="직접 선택한 범위" if selected else "자동: 2항부터 붙임 끝까지")
        ttk.Combobox(bar,textvariable=self.mode,values=("자동: 2항부터 붙임 끝까지","직접 선택한 범위"),state="readonly",width=29).pack(side="left")
        self.attach=tk.BooleanVar(value=True);self.spacing=tk.BooleanVar(value=True)
        ttk.Label(bar,text="제목·1항 제외 / 붙임 포함 / 띄어쓰기 자동 정리").pack(side="left",padx=8)
        ttk.Button(bar,text="다시 정리",command=self.prepare).pack(side="right")
        panes=ttk.PanedWindow(box,orient="horizontal");panes.pack(fill="both",expand=True,pady=10)
        left,right=ttk.Frame(panes),ttk.Frame(panes);panes.add(left,weight=1);panes.add(right,weight=1)
        from pathlib import Path
        ttk.Label(left,text=Path(source_file['path']).name if source_file else '공문 원문',wraplength=470).pack(fill='x',pady=(0,4))
        self.original_tabs=ttk.Notebook(left);self.original_tabs.pack(fill='both',expand=True,padx=(0,6))
        original=ttk.Frame(self.original_tabs);raw=ttk.Frame(self.original_tabs)
        self.original_tabs.add(original,text='공문 원본');self.original_tabs.add(raw,text='OCR 원문·범위 수정')
        from source_document_view import SourceDocumentView
        self.original=SourceDocumentView(original,self.request_preview,thumbnail=bool(source_file and Path(source_file['path']).suffix.lower()!='.pdf'));self.original.pack(fill='both',expand=True)
        ttk.Label(right,text="자동 정리된 본문 — 필요한 부분만 수정한 뒤 복사하세요").pack(anchor="w")
        self.source=tk.Text(raw,wrap="word",font=("맑은 고딕",10),exportselection=False,undo=True);self.source.pack(fill="both",expand=True);self.source.insert("1.0",text);self.source.edit_reset()
        if selected:self.source.tag_add('sel','1.0','end-1c')
        if not source_file:self.original_tabs.select(raw)
        self.result=tk.Text(right,wrap="word",font=("맑은 고딕",11),undo=True);self.result.pack(fill="both",expand=True,padx=(6,0))
        self.result.insert('1.0','2항부터 붙임 끝까지 추출하고 띄어쓰기를 자동으로 정리하는 중…');self.result.configure(state='disabled')
        self.status=tk.StringVar(value="본문 추출과 한국어 띄어쓰기 정리를 준비합니다.")
        ttk.Label(box,textvariable=self.status,wraplength=1060).pack(fill="x",pady=6)
        ttk.Label(box,text="글자·숫자·문장부호는 바꾸지 않습니다. HE/Eo 등 잘못 읽힌 글자는 원본과 대조해 수정하세요.",wraplength=1060).pack(fill="x")
        actions=ttk.Frame(box);actions.pack(fill="x",pady=10)
        self.actions=[]
        for label,command in (("본문 복사",self.copy),*((('발신 본문 선택 위치에 넣기',self.insert),) if on_insert else ())):
            b=ttk.Button(actions,text=label,command=command,state='disabled');b.pack(side="left",padx=5);self.actions.append(b)
        ttk.Button(actions,text='닫기',command=self.close).pack(side='left',padx=5)
        if on_hwp:
            b=ttk.Button(actions,text="복사 + 발신 HWP 저장·열기",command=self.open_hwp,state='disabled');b.pack(side="right");self.actions.append(b)
        self.after=self.win.after(100,self.prepare)
        if source_file:self.request_preview(0)

    def prepare(self):
        self.request=uuid.uuid4().hex
        mode=self.mode.get()
        text=self.source.get('1.0','end')
        if mode=='직접 선택한 범위':
            try:text=self.source.get('sel.first','sel.last')
            except tk.TclError:
                self.status.set('왼쪽 OCR 원문 탭에서 2항부터 붙임 끝까지 사용할 글을 선택하세요.');return
        self.ready=False;self.result.configure(state='disabled')
        self.app.busy=True
        self.status.set("필요한 본문과 띄어쓰기를 정리하는 중…")
        for b in self.actions:b.configure(state="disabled")
        self.app.commands.put(("ocr_clean",{"owner":self.owner,"request":self.request,"text":text,"start":None if mode=='직접 선택한 범위' else 2,"attachments":True,"spacing":True}))

    def request_preview(self,page):
        if not self.source_file:return
        self.preview_request=uuid.uuid4().hex
        self.app.commands.put(('ocr_preview',{'owner':self.owner,'request':self.preview_request,'file':self.source_file,'page':page}))

    def deliver_preview(self,data):
        if data.get('owner')!=self.owner or data.get('request')!=self.preview_request:
            if data.get('image') is not None:data['image'].close()
            return
        self.original.show(data.get('image'),data.get('page_count',1),data.get('error',''))
        if data.get('image') is None:self.original_tabs.select(1)

    def deliver(self,data):
        if data["owner"]!=self.owner or data["request"]!=self.request:return
        if data.get("error"):
            self.ready=False
            range_error='2항' in data['error']
            self.result.configure(state='normal');self.result.delete('1.0','end');self.result.insert('1.0','2항부터 붙임 끝까지의 범위를 확인해 주세요.' if range_error else '자동 정리를 완료하지 못했습니다. 다시 정리한 뒤 복사해 주세요.');self.result.configure(state='disabled')
            self.status.set(data['error']+(' 왼쪽 OCR 원문에서 2항 위치를 확인하거나 필요한 범위를 선택해 주세요.' if range_error else ' 다시 정리를 눌러 재시도하세요.'))
            if range_error:self.original_tabs.select(1)
            return
        self.ready=True;self.result.configure(state='normal')
        for b in self.actions:b.configure(state="normal")
        self.result.delete("1.0","end");self.result.insert("1.0",data["text"])
        self.result.edit_reset()
        warnings=data.get("warnings",[])
        for token in warnings:
            start="1.0"
            while True:
                found=self.result.search(token,start,stopindex="end")
                if not found:break
                end=f"{found}+{len(token)}c";self.result.tag_add("check",found,end);start=end
        self.result.tag_configure("check",background="#fff0b3",foreground="#903b00")
        self.status.set("정리했습니다. 확인할 OCR 글자: "+", ".join(warnings) if warnings else "정리했습니다. 문서번호·날짜·수량과 문장을 확인하고 사용하세요.")

    def copy(self):
        if not self.ready:return
        text=self.result.get("1.0","end").strip()
        if not text:return messagebox.showinfo("본문 확인","정리 결과를 먼저 준비해 주세요.",parent=self.win)
        self.win.clipboard_clear();self.win.clipboard_append(text);self.win.update_idletasks()
        self.status.set("복사했습니다. 한글에서 원하는 위치에 Ctrl+V로 넣으세요.")
        return text

    def insert(self):
        if not self.ready:return
        text=self.result.get("1.0","end").strip()
        if text and self.on_insert:self.on_insert(text);self.close()

    def open_hwp(self):
        if self.copy() and self.on_hwp:
            callback=self.on_hwp;self.close();callback()

    def close(self):
        self.win.after_cancel(self.after);self.original.close();self.win.destroy();self.app.ocr_text_editor=None
        if isinstance(self.parent,tk.Toplevel) and self.parent.winfo_exists():self.parent.grab_set()
