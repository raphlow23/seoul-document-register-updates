"""Review an OCR excerpt and spacing changes before copying or inserting into a draft."""
import tkinter as tk
from tkinter import ttk,messagebox
import uuid


class OcrTextEditor:
    def __init__(self,app,text,parent=None,selected=False,on_insert=None,on_hwp=None):
        self.app,self.parent=app,parent or app.root
        self.owner=uuid.uuid4().hex;self.request="";self.on_insert=on_insert;self.on_hwp=on_hwp
        self.win=tk.Toplevel(self.parent);self.win.title("필요한 본문만 추출 · 띄어쓰기 정리")
        self.win.geometry("1120x730");self.win.transient(self.parent);self.win.grab_set();self.win.protocol("WM_DELETE_WINDOW",self.close)
        app.ocr_text_editor=self
        box=ttk.Frame(self.win,padding=14);box.pack(fill="both",expand=True)
        bar=ttk.Frame(box);bar.pack(fill="x")
        self.mode=tk.StringVar(value="선택한 글 그대로" if selected else "2항부터 (인사말 제외)")
        ttk.Combobox(bar,textvariable=self.mode,values=("2항부터 (인사말 제외)","1항부터","선택한 글 그대로"),state="readonly",width=25).pack(side="left")
        self.attach=tk.BooleanVar(value=True);self.spacing=tk.BooleanVar(value=True)
        ttk.Checkbutton(bar,text="붙임 포함",variable=self.attach).pack(side="left",padx=6)
        ttk.Checkbutton(bar,text="띄어쓰기 정리",variable=self.spacing).pack(side="left")
        ttk.Button(bar,text="다시 정리",command=self.prepare).pack(side="right")
        panes=ttk.PanedWindow(box,orient="horizontal");panes.pack(fill="both",expand=True,pady=10)
        left,right=ttk.Frame(panes),ttk.Frame(panes);panes.add(left,weight=1);panes.add(right,weight=1)
        ttk.Label(left,text="OCR 원문 — 범위를 직접 수정할 수 있습니다").pack(anchor="w")
        ttk.Label(right,text="정리 결과 — 확인하고 수정한 뒤 사용하세요").pack(anchor="w")
        self.source=tk.Text(left,wrap="word",font=("맑은 고딕",10));self.source.pack(fill="both",expand=True,padx=(0,6));self.source.insert("1.0",text)
        self.result=tk.Text(right,wrap="word",font=("맑은 고딕",11));self.result.pack(fill="both",expand=True,padx=(6,0))
        self.status=tk.StringVar(value="본문 추출과 한국어 띄어쓰기 정리를 준비합니다.")
        ttk.Label(box,textvariable=self.status,wraplength=1060).pack(fill="x",pady=6)
        ttk.Label(box,text="글자·숫자·문장부호는 바꾸지 않습니다. HE/Eo 등 잘못 읽힌 글자는 원본과 대조해 수정하세요.",wraplength=1060).pack(fill="x")
        actions=ttk.Frame(box);actions.pack(fill="x",pady=10)
        self.actions=[]
        for label,command in (("정리한 본문 복사",self.copy),("발신 본문 선택 위치에 넣기",self.insert) if on_insert else ("닫기",self.close)):
            b=ttk.Button(actions,text=label,command=command);b.pack(side="left",padx=5);self.actions.append(b)
        if on_hwp:
            b=ttk.Button(actions,text="복사 + 발신 HWP 저장·열기",command=self.open_hwp);b.pack(side="right");self.actions.append(b)
        self.after=self.win.after(100,self.prepare)

    def prepare(self):
        self.request=uuid.uuid4().hex
        mode=self.mode.get()
        self.app.busy=True
        self.status.set("필요한 본문과 띄어쓰기를 정리하는 중…")
        for b in self.actions:b.configure(state="disabled")
        self.app.commands.put(("ocr_clean",{"owner":self.owner,"request":self.request,"text":self.source.get("1.0","end"),"start":2 if mode.startswith("2") else 1 if mode.startswith("1") else None,"attachments":self.attach.get(),"spacing":self.spacing.get()}))

    def deliver(self,data):
        if data["owner"]!=self.owner or data["request"]!=self.request:return
        for b in self.actions:b.configure(state="normal")
        if data.get("error"):self.status.set(data["error"]);return
        self.result.delete("1.0","end");self.result.insert("1.0",data["text"])
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
        text=self.result.get("1.0","end").strip()
        if not text:return messagebox.showinfo("본문 확인","정리 결과를 먼저 준비해 주세요.",parent=self.win)
        self.win.clipboard_clear();self.win.clipboard_append(text);self.win.update_idletasks()
        self.status.set("복사했습니다. 한글에서 원하는 위치에 Ctrl+V로 넣으세요.")
        return text

    def insert(self):
        text=self.result.get("1.0","end").strip()
        if text and self.on_insert:self.on_insert(text);self.close()

    def open_hwp(self):
        if self.copy() and self.on_hwp:
            callback=self.on_hwp;self.close();callback()

    def close(self):
        self.win.after_cancel(self.after);self.win.destroy();self.app.ocr_text_editor=None
        if isinstance(self.parent,tk.Toplevel) and self.parent.winfo_exists():self.parent.grab_set()
