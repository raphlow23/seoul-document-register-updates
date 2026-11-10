"""Read-only original-document previews alongside the editable OCR result."""
from pathlib import Path
import io
import tkinter as tk
from tkinter import ttk


def load(path,page=0):
    path=Path(path)
    from attachment_files import document_kind
    kind=document_kind(path)
    if kind=='.pdf':
        from title_tools import pdf_preview
        return pdf_preview(path,page)
    raw=None
    if kind=='.hwp':
        import olefile
        with olefile.OleFileIO(path) as ole:
            if ole.exists('PrvImage'):raw=ole.openstream('PrvImage').read(8*1024*1024+1)
    elif kind=='.hwpx':
        import zipfile
        if not zipfile.is_zipfile(path):return None,1
        with zipfile.ZipFile(path) as archive:
            name=next((n for n in archive.namelist() if n.lower() in ('preview/prvimage.png','preview/prvimage.jpg','preview/prvimage.jpeg')),None)
            if name and archive.getinfo(name).file_size<=8*1024*1024:
                with archive.open(name) as stream:raw=stream.read(8*1024*1024+1)
    if not raw or len(raw)>8*1024*1024:return None,1
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height>16_000_000:raise ValueError('원본 미리보기 이미지가 너무 큽니다.')
        return image.convert('RGB').copy(),1


class SourceDocumentView(ttk.Frame):
    def __init__(self,parent,request,thumbnail=False):
        super().__init__(parent);self.request=request;self.page=0;self.count=1;self.zoom=1.;self.image=self.photo=None
        self.thumbnail=thumbnail
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Button(bar,text='이전 쪽',command=lambda:self.turn(-1)).pack(side='left')
        ttk.Button(bar,text='다음 쪽',command=lambda:self.turn(1)).pack(side='left',padx=4)
        ttk.Button(bar,text='확대 +',command=lambda:self.scale(1.25)).pack(side='left')
        ttk.Button(bar,text='축소 −',command=lambda:self.scale(.8)).pack(side='left',padx=4)
        self.label=tk.StringVar(value='원본을 불러오는 중…');ttk.Label(self,textvariable=self.label,wraplength=440).pack(fill='x',pady=4)
        area=ttk.Frame(self);area.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(area,bg='#e6eaf0',highlightthickness=0,width=470)
        v=ttk.Scrollbar(area,command=self.canvas.yview);h=ttk.Scrollbar(area,orient='horizontal',command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=v.set,xscrollcommand=h.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');v.grid(row=0,column=1,sticky='ns');h.grid(row=1,column=0,sticky='ew');area.rowconfigure(0,weight=1);area.columnconfigure(0,weight=1)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120)*3,'units'))
        self.canvas.bind('<Configure>',lambda e:self.draw())

    def turn(self,delta):
        if 0<=self.page+delta<self.count:self.page+=delta;self.request(self.page)

    def scale(self,factor):self.zoom=max(.4,min(4.,self.zoom*factor));self.draw()

    def show(self,image,count,error=''):
        if self.image is not None:self.image.close()
        self.image=image;self.count=count
        self.label.set(('한글 원본 첫 화면 미리보기 · 전체 문장은 OCR 원문 탭에서 확인하세요.' if self.thumbnail else f'{self.page+1}/{count}쪽 · 원본은 수정하지 않습니다.') if image else error or '미리보기가 없는 문서입니다. OCR 원문 탭에서 전체 내용을 확인하세요.')
        self.draw()

    def draw(self):
        self.canvas.delete('all')
        if self.image is None:return
        from PIL import Image,ImageTk
        ratio=max(.1,(self.canvas.winfo_width()-15)/self.image.width)*self.zoom
        with self.image.resize((max(1,int(self.image.width*ratio)),max(1,int(self.image.height*ratio))),Image.Resampling.LANCZOS) as image:self.photo=ImageTk.PhotoImage(image,master=self)
        self.canvas.create_image(0,0,anchor='nw',image=self.photo);self.canvas.configure(scrollregion=(0,0,self.photo.width(),self.photo.height()))

    def close(self):
        if self.image is not None:self.image.close();self.image=None
        self.photo=None
