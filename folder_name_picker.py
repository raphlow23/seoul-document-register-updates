"""Present concrete folder names; format tokens stay inside the program."""
import re
import string
import tkinter as tk
from tkinter import ttk,simpledialog
from folder_naming import TEMPLATES,folder_name,infer_template,sequence_from_names


def split_name(name):
    template=infer_template(name) or '{title}'
    pattern=''
    for literal,field,_,_ in string.Formatter().parse(template):
        pattern+=re.escape(literal)
        if field:pattern+='(?P<title>.+)' if field=='title' else r'\d+' if field not in ('date','dotdate','ymd') else {'date':r'\d{4}-\d{2}-\d{2}','dotdate':r'\d{4}\.\d{2}\.\d{2}','ymd':r'\d{8}'}[field]
    match=re.fullmatch(pattern,name)
    return (template,match['title']) if match else ('{title}',name)


class ActualNamePicker(ttk.Frame):
    def __init__(self,parent,template,title,record,day,reserved=lambda:None):
        super().__init__(parent)
        self.template,self.title,self.record,self.day,self.reserved=template,title,record,day,reserved
        self.value=tk.StringVar();self.options={}
        self.box=ttk.Combobox(self,textvariable=self.value,state='readonly');self.box.pack(side='left',fill='x',expand=True)
        self.box.bind('<<ComboboxSelected>>',self.choose)
        ttk.Button(self,text='직접 수정',command=self.edit).pack(side='right',padx=(5,0))

    def refresh(self):
        record=self.record()
        if not record:return
        choices=[self.template.get(),*[entry['template'] for entry in record.get('styles',[])],*TEMPLATES]
        values={}
        for template in choices:
            if not template:continue
            try:
                ordinal=self.reserved() or sequence_from_names(record['names'],template)
                name=folder_name(template,self.title.get(),self.day(),ordinal)
                values.setdefault(name,template)
            except (ValueError,TypeError,KeyError):continue
        self.options=values;self.box.configure(values=list(values))
        current=next((name for name,template in values.items() if template==self.template.get()),'')
        self.value.set(current)

    def choose(self,_=None):
        if self.value.get() in self.options:self.template.set(self.options[self.value.get()])

    def edit(self):
        name=simpledialog.askstring('폴더 이름 수정','실제 폴더 이름을 수정하세요. 번호·날짜는 저장 위치의 다음 순번과 해당 날짜로 계산합니다.',initialvalue=self.value.get(),parent=self.winfo_toplevel())
        if not name or not name.strip():return
        template,title=split_name(name.strip())
        self.title.set(title);self.template.set(template);self.refresh()
