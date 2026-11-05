"""Keep default child folders, append custom names and arrange their order."""
import tkinter as tk
from tkinter import ttk,simpledialog,messagebox
from folder_naming import child_folders,safe_component


class ChildFolderEditor:
    def __init__(self,parent,agency,order,selected,on_apply):
        self.agency,self.order,self.selected,self.on_apply=agency,list(order),set(selected),on_apply
        self.previous_grab=parent.grab_current()
        self.win=tk.Toplevel(parent);self.win.title('내부 폴더 추가 · 순서 변경');self.win.geometry('720x490')
        self.win.transient(parent.winfo_toplevel());self.win.grab_set();self.win.protocol('WM_DELETE_WINDOW',self.close)
        outer=ttk.Frame(self.win,padding=16);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='기본 폴더는 유지하고 필요한 폴더를 추가하세요.\n체크한 폴더만 위에서부터 1번, 2번 순서로 만듭니다.',wraplength=530).pack(anchor='w',pady=(0,10))
        area=ttk.Frame(outer);area.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(area,columns=('name',),show='tree headings',selectmode='browse',height=10)
        self.tree.heading('#0',text='생성');self.tree.column('#0',width=60,stretch=False)
        self.tree.heading('name',text='내부 폴더 이름 · 위에서부터 생성 순서');self.tree.column('name',width=360)
        scroll=ttk.Scrollbar(area,command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<ButtonRelease-1>',self.click);self.tree.bind('<space>',lambda e:self.toggle())
        controls=ttk.Frame(outer);controls.pack(fill='x',pady=10)
        for text,action in (('폴더 추가',self.add),('선택 체크/해제',self.toggle),('위로 ↑',lambda:self.move(-1)),('아래로 ↓',lambda:self.move(1)),('추가 폴더 삭제',self.remove)):
            ttk.Button(controls,text=text,command=action).pack(side='left',padx=2)
        ttk.Label(outer,text='기본 폴더는 삭제하지 않으며 체크 해제할 수 있습니다. 추가 폴더 이름에는 번호를 붙이지 않아도 됩니다.',wraplength=530).pack(anchor='w')
        footer=ttk.Frame(outer);footer.pack(fill='x',pady=(12,0))
        ttk.Button(footer,text='취소',command=self.close).pack(side='left')
        ttk.Button(footer,text='이 구성 사용',command=self.apply).pack(side='right')
        self.refresh()

    def refresh(self,focus=None):
        for key in self.tree.get_children():self.tree.delete(key)
        labels=child_folders(self.agency,self.order,self.order)
        number=0
        for key in self.order:
            if key in self.selected:number+=1
            label=labels[key].split('.',1)[1]
            self.tree.insert('','end',iid=key,text='☑' if key in self.selected else '☐',values=((f'{number}. ' if key in self.selected else '')+label,))
        if focus and self.tree.exists(focus):self.tree.selection_set(focus);self.tree.see(focus)

    def click(self,event):
        if self.tree.identify_column(event.x)=='#0':self.toggle(self.tree.identify_row(event.y))

    def toggle(self,key=None):
        key=key or next(iter(self.tree.selection()),None)
        if not key:return
        if key in self.selected:self.selected.remove(key)
        else:self.selected.add(key)
        self.refresh(key)

    def add(self):
        name=simpledialog.askstring('폴더 추가','추가할 내부 폴더 이름을 입력하세요.',parent=self.win)
        if not name or not name.strip():return
        try:name=safe_component(name)
        except ValueError as error:return messagebox.showinfo('폴더 이름',str(error),parent=self.win)
        labels=[v.split('.',1)[1].casefold() for v in child_folders(self.agency,self.order,self.order).values()]
        if name.casefold() in labels:return messagebox.showinfo('폴더 이름','같은 이름의 폴더가 이미 있습니다.',parent=self.win)
        key='custom:'+name;self.order.append(key);self.selected.add(key);self.refresh(key)

    def move(self,delta):
        key=next(iter(self.tree.selection()),None)
        if not key:return
        index=self.order.index(key);target=index+delta
        if 0<=target<len(self.order):self.order[index],self.order[target]=self.order[target],self.order[index];self.refresh(key)

    def remove(self):
        key=next(iter(self.tree.selection()),None)
        if not key:return
        if not key.startswith('custom:'):return messagebox.showinfo('기본 폴더','기본 폴더는 유지합니다. 만들지 않으려면 체크를 해제하세요.',parent=self.win)
        self.order.remove(key);self.selected.discard(key);self.refresh()

    def apply(self):
        self.on_apply(list(self.order),[key for key in self.order if key in self.selected]);self.close()

    def close(self):
        self.win.destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():self.previous_grab.grab_set()
