"""One native ttk theme with rounded controls, clear focus states and image checkboxes."""
import tkinter as tk
from tkinter import ttk
from PIL import Image,ImageDraw,ImageTk

BG='#F3F6FB';WHITE='#FFFFFF';INK='#18283F';MUTED='#65758C';BLUE='#2563EB';LINE='#DAE3EF'


def apply_theme(root):
    style=ttk.Style(root)
    if getattr(root,'_modern_theme_assets',None):style.theme_use('seoul-modern');return style
    assets={}
    def picture(name,fill,border=LINE,kind='box',checked=False,size=(40,40)):
        scale=3;w,h=size;im=Image.new('RGBA',(w*scale,h*scale));draw=ImageDraw.Draw(im)
        rect=(scale,scale,(w-1)*scale,(h-1)*scale)
        if kind=='radio':
            draw.ellipse(rect,fill=fill,outline=border,width=scale)
            if checked:draw.ellipse((7*scale,7*scale,(w-7)*scale,(h-7)*scale),fill=BLUE)
        else:
            draw.rounded_rectangle(rect,radius=(6 if kind=='check' else 11)*scale,fill=fill,outline=border,width=scale)
            if checked:draw.line([(5*scale,11*scale),(9*scale,15*scale),(17*scale,7*scale)],fill='white',width=2*scale,joint='curve')
        im=im.resize((w,h),Image.Resampling.LANCZOS)
        if kind in ('check','radio'):
            padded=Image.new('RGBA',(w+6,h));padded.paste(im,(0,0));im.close();im=padded
        photo=ImageTk.PhotoImage(im,master=root);im.close();assets[name]=photo;return photo
    for name,fill,border in (('button',WHITE,LINE),('hover','#EFF5FF','#9DBBF5'),('pressed','#E2ECFF','#7EA5EE'),('disabled','#F1F4F8','#E3E9F2'),('accent',BLUE,BLUE),('accent_hover','#1D4ED8','#1D4ED8'),('accent_pressed','#1E40AF','#1E40AF'),('entry',WHITE,LINE),('focus',WHITE,'#6695F0'),('card',WHITE,LINE),('drop','#F0F6FF','#C8DAFA')):
        picture(name,fill,border)
    for name,fill,border,checked in (('check_off',WHITE,'#A6B6CC',False),('check_on',BLUE,BLUE,True),('check_disabled','#F2F5FA','#D0DAE7',False),('check_disabled_on','#9CB8EC','#9CB8EC',True)):
        picture(name,fill,border,kind='check',checked=checked,size=(22,22))
    picture('radio_off',WHITE,'#A6B6CC',kind='radio',size=(22,22));picture('radio_on',WHITE,BLUE,kind='radio',checked=True,size=(22,22))
    root._modern_theme_assets=assets
    if 'seoul-modern' not in style.theme_names():style.theme_create('seoul-modern',parent='clam')
    style.theme_use('seoul-modern')
    def element(name,default,*states,border=11):
        style.element_create(name,'image',assets[default],*((*state,assets[photo]) for *state,photo in states),border=border,sticky='nswe')
    element('Modern.Button','button',('disabled','disabled'),('pressed','pressed'),('active','hover'),('focus','hover'))
    element('Modern.Accent','accent',('disabled','disabled'),('pressed','accent_pressed'),('active','accent_hover'))
    element('Modern.Selected','accent')
    element('Modern.Entry','entry',('disabled','disabled'),('focus','focus'))
    element('Modern.Card','card');element('Modern.Drop','drop')
    element('Modern.Check','check_off',('disabled','selected','check_disabled_on'),('disabled','check_disabled'),('selected','check_on'),border=0)
    element('Modern.Radio','radio_off',('selected','radio_on'),border=0)
    for name,border in (('TButton','Modern.Button'),('Accent.TButton','Modern.Accent'),('NavSelected.TButton','Modern.Selected')):
        style.layout(name,[(border,{'sticky':'nswe','children':[('Button.padding',{'sticky':'nswe','children':[('Button.label',{'sticky':'nswe'})]})]})])
    style.layout('TEntry',[('Modern.Entry',{'sticky':'nswe','children':[('Entry.padding',{'sticky':'nswe','children':[('Entry.textarea',{'sticky':'nswe'})]})]})])
    style.layout('TCombobox',[('Modern.Entry',{'sticky':'nswe','children':[('Combobox.downarrow',{'side':'right','sticky':'ns'}),('Combobox.padding',{'sticky':'nswe','children':[('Combobox.textarea',{'sticky':'nswe'})]})]})])
    for name,indicator,base in (('TCheckbutton','Modern.Check','Checkbutton'),('TRadiobutton','Modern.Radio','Radiobutton')):
        style.layout(name,[(base+'.padding',{'sticky':'nswe','children':[(indicator,{'side':'left','sticky':''}),(base+'.label',{'side':'left','sticky':'w'})]})])
    style.layout('Card.TFrame',[('Modern.Card',{'sticky':'nswe'})])
    style.layout('Drop.TLabel',[('Modern.Drop',{'sticky':'nswe','children':[('Label.padding',{'sticky':'nswe','children':[('Label.label',{'sticky':'nswe'})]})]})])
    font=('맑은 고딕',10)
    style.configure('.',font=font,background=WHITE,foreground=INK,bordercolor=LINE,lightcolor=WHITE,darkcolor=LINE,focuscolor=BLUE)
    style.configure('TFrame',background=WHITE);style.configure('App.TFrame',background=BG);style.configure('App.TLabel',background=BG)
    style.configure('TLabel',background=WHITE,padding=0)
    style.configure('TButton',padding=(11,4),font=font,foreground=INK,background=WHITE,anchor='center')
    style.map('TButton',foreground=[('disabled','#94A1B3')])
    style.configure('Accent.TButton',foreground=WHITE,font=('맑은 고딕',10,'bold'))
    style.map('Accent.TButton',foreground=[('disabled','#94A1B3'),('!disabled',WHITE)])
    style.configure('NavSelected.TButton',foreground=WHITE,font=('맑은 고딕',10,'bold'))
    style.map('NavSelected.TButton',foreground=[('disabled',WHITE),('!disabled',WHITE)])
    style.configure('TEntry',padding=(9,3),fieldbackground=WHITE,foreground=INK,insertcolor=INK)
    style.configure('TCombobox',padding=(9,3),arrowsize=16,fieldbackground=WHITE,foreground=INK,background=WHITE,arrowcolor=MUTED,bordercolor=WHITE,lightcolor=WHITE,darkcolor=WHITE)
    style.map('TCombobox',fieldbackground=[('readonly',WHITE)],selectbackground=[('readonly',WHITE)],selectforeground=[('readonly',INK)])
    style.configure('TCheckbutton',padding=(0,5,8,5),background=WHITE,foreground=INK)
    style.configure('TRadiobutton',padding=(0,5,8,5),background=WHITE,foreground=INK)
    style.map('TCheckbutton',foreground=[('disabled','#94A1B3')]);style.map('TRadiobutton',foreground=[('disabled','#94A1B3')])
    style.configure('Treeview',background=WHITE,fieldbackground=WHITE,foreground=INK,rowheight=38,borderwidth=0,relief='flat',font=font)
    style.map('Treeview',background=[('selected','#E8F0FF')],foreground=[('selected','#1747A6')])
    style.configure('Treeview.Heading',background='#F4F7FC',foreground=MUTED,font=('맑은 고딕',10,'bold'),padding=(10,11),borderwidth=0,relief='flat')
    style.map('Treeview.Heading',background=[('active','#ECF2FA')])
    style.configure('TLabelframe',borderwidth=1,relief='flat',background=WHITE,bordercolor=LINE)
    style.configure('TLabelframe.Label',foreground=MUTED,font=('맑은 고딕',10,'bold'))
    style.configure('Drop.TLabel',foreground='#285CAF',padding=16,anchor='center')
    style.configure('Vertical.TScrollbar',background='#C7D4E7',troughcolor='#F2F5FA',borderwidth=0,arrowsize=12)
    style.configure('Horizontal.TScrollbar',background='#C7D4E7',troughcolor='#F2F5FA',borderwidth=0,arrowsize=12)
    for pattern,value in (('*Text.background',WHITE),('*Text.foreground',INK),('*Text.relief','flat'),('*Text.borderWidth',0),('*Text.highlightThickness',1),('*Text.highlightBackground',LINE),('*Text.highlightColor','#6695F0'),('*Text.selectBackground','#DCE9FF'),('*Text.selectForeground',INK),('*Listbox.background',WHITE),('*Listbox.foreground',INK),('*Listbox.relief','flat'),('*Listbox.borderWidth',0),('*Listbox.highlightThickness',1),('*Listbox.highlightBackground',LINE),('*Listbox.selectBackground','#E8F0FF'),('*Listbox.selectForeground','#1747A6'),('*TCombobox*Listbox.background',WHITE),('*TCombobox*Listbox.foreground',INK)):
        root.option_add(pattern,value)
    root.configure(background=BG)
    return style


class CheckboxTree(ttk.Treeview):
    def _choice(self,kw):
        text=kw.get('text','');assets=getattr(self._root(),'_modern_theme_assets',None)
        if assets and text and text[0] in ('☐','☑'):
            kw['image']=assets['check_on' if text[0]=='☑' else 'check_off'];kw['text']=text[1:].lstrip()
        return kw

    def insert(self,parent,index,iid=None,**kw):return super().insert(parent,index,iid=iid,**self._choice(kw))

    def item(self,item,option=None,**kw):return super().item(item,option,**self._choice(kw))


class ScrollablePanel(ttk.Frame):
    """Keep settings reachable while the final folder preview stays visible."""
    def __init__(self,parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,background=WHITE,highlightthickness=0,borderwidth=0,height=200)
        scrollbar=ttk.Scrollbar(self,command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left',fill='both',expand=True);scrollbar.pack(side='right',fill='y')
        self.body=ttk.Frame(self.canvas);self.window=self.canvas.create_window((0,0),window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _ :self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.after_idle(self.bind_children)

    def bind_children(self):
        def visit(widget):
            if not isinstance(widget,(tk.Text,tk.Listbox,ttk.Treeview)):
                widget.bind('<MouseWheel>',self.wheel,add='+')
                for child in widget.winfo_children():visit(child)
        visit(self.body)

    def wheel(self,event):
        if self.body.winfo_reqheight()>self.canvas.winfo_height():self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
        return 'break'
