"""Scrollable, explicit column and default-value editor for one ledger role."""
from copy import deepcopy
from datetime import date
import tkinter as tk
from tkinter import ttk,messagebox
from ledger_layout import LABELS,DATE_FORMATS,schema,normalize,normalize_values,default_values


def open_editor(app,parent,scope,family,layout,defaults,on_apply,readonly=False,show_defaults=True):
    win=tk.Toplevel(parent);win.title(f'{family} · '+{'receipt':'수신','outgoing':'발신','stamp':'직인'}[scope]+' 입력 설정')
    win.geometry(f'900x{min(760,win.winfo_screenheight()-100)}');win.minsize(740,460);win.transient(parent);win.grab_set()
    footer=ttk.Frame(win,padding=12);footer.pack(side='bottom',fill='x')
    from modern_ui import ScrollablePanel
    panel=ScrollablePanel(win);panel.pack(fill='both',expand=True);body=panel.body;body.columnconfigure(1,weight=1);body.columnconfigure(2,weight=2)
    ttk.Label(body,text='구글 시트의 실제 열 문자와 기본 입력값을 지정하세요. 예: 수신처 I → J.\n기본 입력값은 새 공문에 먼저 채워지며, 등록 전에 문서별로 수정할 수 있습니다.\n제목·날짜·번호는 문서 정보와 시트의 실제 번호를 사용합니다. 번호 수식과 기존 서식은 유지합니다.',wraplength=810).grid(row=0,column=0,columnspan=3,sticky='ew',padx=14,pady=12)
    current=normalize(layout,scope,family)
    header=tk.StringVar(value=current['header_range']);start=tk.StringVar(value=str(current['data_start']));fmt=tk.StringVar(value=current['date_format'])
    meta=ttk.Frame(body);meta.grid(row=1,column=0,columnspan=3,sticky='ew',padx=14,pady=8)
    for i,(label,var,width) in enumerate((('머리글 범위 (예: A3:K4)',header,16),('첫 데이터 행 (예: 5)',start,8))):
        ttk.Label(meta,text=label).grid(row=i,column=0,sticky='w',pady=6)
        ttk.Entry(meta,textvariable=var,width=width,state='readonly' if readonly else 'normal').grid(row=i,column=1,sticky='w',padx=12)
    ttk.Label(meta,text='둘 다 비우면 현재처럼 머리글을 자동으로 찾습니다.',wraplength=420).grid(row=2,column=0,columnspan=2,sticky='w',pady=6)
    ttk.Label(meta,text='날짜 표시 형식').grid(row=3,column=0,sticky='w',pady=6)
    ttk.Combobox(meta,textvariable=fmt,values=DATE_FORMATS,state='disabled' if readonly else 'readonly',width=22).grid(row=3,column=1,sticky='w',padx=12)
    fields={};values={};effective=default_values(app.config,scope,family);effective.update(defaults or {})
    original=deepcopy(defaults or {});initial={k:str(v) for k,v in effective.items()}
    for c,label in enumerate(('입력 항목','구글 시트 열 (A~AZ)','새 문서의 기본 입력값')):ttk.Label(body,text=label).grid(row=2,column=c,sticky='w',padx=14,pady=8)
    for row,key in enumerate(schema(scope,family),3):
        ttk.Label(body,text=LABELS[key]).grid(row=row,column=0,sticky='w',padx=14,pady=6)
        fields[key]=tk.StringVar(value=current['columns'][key])
        ttk.Entry(body,textvariable=fields[key],width=9,state='readonly' if readonly else 'normal').grid(row=row,column=1,sticky='w',padx=14,pady=6)
        if key in effective and show_defaults:
            values[key]=tk.StringVar(value=effective[key])
            widget=ttk.Combobox(body,textvariable=values[key],values=('Email','문서24','우편','FAX','인편') if family=='공문' else ('Email','우편','FAX','인편'),state='disabled' if readonly else 'readonly') if key=='method' else ttk.Entry(body,textvariable=values[key],state='readonly' if readonly else 'normal')
            widget.grid(row=row,column=2,sticky='ew',padx=14,pady=6)
        else:
            text={'date':'문서에서 선택한 날짜 (기본 오늘)','title':'문서에서 수정한 최종 제목','number':'시트에서 확인한 실제 번호','serial':'이전 연번 + 1','prefix':'이전 행의 번호 앞 문구 유지','document_number':'확정된 발신 문서번호','receiver':'발신 정보에서 입력한 수신처'}.get(key,'문서 작성 화면에서 수정' if key in effective else '선택한 공문 분류에만 ○ 표시')
            ttk.Label(body,text=text,wraplength=380).grid(row=row,column=2,sticky='w',padx=14,pady=6)
    row=3+len(fields)
    ttk.Label(body,text='신청자 기본값을 비우면 해당 공문의 발송자를 사용합니다. 날인자는 공란으로 둘 수 있습니다.\n열 순서가 바뀐 뒤에는 설정을 저장하고 「읽기 확인」으로 머리글을 먼저 검사하세요.\n첫 데이터 행은 표의 시작 위치이며 새 등록은 마지막 사용 행 다음에 입력합니다.\n자동 등록을 시작한 문서는 당시 열 설정을 유지합니다.',wraplength=800).grid(row=row,column=0,columnspan=3,sticky='ew',padx=14,pady=14)
    win.layout_fields=fields;win.default_fields=values;win.header_range=header;win.data_start=start;win.date_format=fmt
    def close():
        win.destroy()
        if parent.winfo_exists():parent.grab_set()
    def apply():
        try:
            result=normalize({'columns':{k:v.get() for k,v in fields.items()},'header_range':header.get(),'data_start':start.get(),'date_format':fmt.get()},scope,family)
            saved={}
            for k,var in values.items():
                text=var.get().strip()
                if text==initial[k]:
                    if k in original:saved[k]=original[k]
                else:saved[k]=text
            saved=normalize_values(saved,scope,family)
        except ValueError as error:return messagebox.showerror('입력 설정',str(error),parent=win)
        on_apply(result,saved);close()
    ttk.Button(footer,text='닫기',command=close).pack(side='left')
    if not readonly:ttk.Button(footer,text='이 설정 적용',command=apply).pack(side='right')
    win.protocol('WM_DELETE_WINDOW',close)
    return win
