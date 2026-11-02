"""Small settings window; connection and deployment controls stay out of document steps."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from outgoing_editor import folder_settings_dialog


def open_settings(app):
    win=tk.Toplevel(app.root);win.title("설정 · Google 연결");win.geometry("900x690")
    win.transient(app.root);win.grab_set()
    outer=ttk.Frame(win,padding=20);outer.pack(fill="both",expand=True)
    tabs=ttk.Notebook(outer);tabs.pack(fill="both",expand=True)
    basic=ttk.Frame(tabs,padding=16);advanced=ttk.Frame(tabs,padding=16)
    tabs.add(basic,text="기본 설정");tabs.add(advanced,text="고급 설정 (필요할 때만)")
    fields={k:tk.StringVar(value=str(app.config.get(k,""))) for k in ("downloads","root","recipient","spreadsheet_id","tab","update_url","oauth_client_path","key_path")}
    def entry(parent,row,key,label,browse=False):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky="w",pady=9)
        ttk.Entry(parent,textvariable=fields[key]).grid(row=row,column=1,sticky="ew",padx=10)
        if browse:
            def choose():
                path=filedialog.askopenfilename(parent=win,filetypes=[("Google 설정","*.json")]) if key.endswith("path") else filedialog.askdirectory(parent=win)
                if path:fields[key].set(path)
            ttk.Button(parent,text="선택",command=choose).grid(row=row,column=2)
        parent.columnconfigure(1,weight=1)
    for row,(key,label) in enumerate((("downloads","다운로드 폴더"),("root","공유 실적 폴더"),("recipient","기본 담당자"),("spreadsheet_id","구글 시트 주소 또는 ID"))):entry(basic,row,key,label,key in ("downloads","root"))
    ttk.Label(basic,text="발신 양식별 기본 저장 위치").grid(row=4,column=0,sticky="w",pady=12)
    ttk.Button(basic,text="6종 양식 저장 위치 설정",command=lambda:folder_settings_dialog(app,win)).grid(row=4,column=1,sticky="w",padx=10)
    connect=ttk.LabelFrame(basic,text="Google 연결",padding=12);connect.grid(row=5,column=0,columnspan=3,sticky="ew",pady=14)
    ttk.Label(connect,text="시트 편집 권한이 있는 계정으로 로그인하세요. 브라우저 방식은 API 설정이 필요 없습니다.",wraplength=760).pack(anchor="w")
    ttk.Label(connect,text="로그인하여 시트가 보이는지 확인 → 열린 전용 브라우저 창 닫기 → 연결 확인",wraplength=760).pack(anchor="w",pady=6)
    row=ttk.Frame(connect);row.pack(fill="x",pady=8)
    mode=tk.StringVar(value=app.config.get("auth_mode","browser"))
    auto_update=tk.BooleanVar(value=app.config.get("auto_update",True))
    for i,(key,label) in enumerate((("tab","접수대장 탭 이름"),("update_url","온라인 업데이트 주소"),("oauth_client_path","OAuth 설정 JSON"),("key_path","서비스 계정 JSON"))):entry(advanced,i,key,label,key.endswith("path"))
    ttk.Label(advanced,text="Google 연결 방식").grid(row=4,column=0,sticky="w",pady=12)
    ttk.Combobox(advanced,textvariable=mode,values=("browser","oauth","service"),state="readonly").grid(row=4,column=1,sticky="ew",padx=10)
    ttk.Label(advanced,text="browser = 일반 브라우저 로그인 / oauth·service = 별도 API 설정",wraplength=750).grid(row=5,column=0,columnspan=3,sticky="w",pady=8)
    ttk.Button(advanced,text="API 계정 로그인",command=lambda:action("oauth")).grid(row=6,column=1,sticky="w",padx=10)
    ttk.Button(advanced,text="이 PC 연결 해제",command=lambda:action("logout")).grid(row=7,column=1,sticky="w",padx=10,pady=12)
    ttk.Checkbutton(basic,text="실행할 때 새 버전 자동 다운로드 · 설치",variable=auto_update).grid(row=6,column=0,columnspan=3,sticky="w",pady=10)
    ttk.Label(basic,textvariable=app.update_status,foreground="#667085",wraplength=760).grid(row=7,column=0,columnspan=3,sticky="w")
    def persist():
        from urllib.parse import urlparse
        values={k:v.get().strip() for k,v in fields.items()}
        source=values["spreadsheet_id"]
        if "/spreadsheets/d/" in source:
            values["spreadsheet_id"]=urlparse(source).path.split("/spreadsheets/d/",1)[1].split("/",1)[0]
        app.config.update(values);app.config.update(auth_mode=mode.get(),auto=False,auto_update=auto_update.get())
        app.persist_config();app.commands.put(("config",app.config.copy()))
    def action(kind):
        if not app.workflow.available():return
        if kind=="browser":mode.set("browser")
        persist();win.destroy()
        # Config is queued ahead of this operation on the same serial worker.
        if kind=="update":app.check_update(manual=True)
        elif kind=="oauth":app.root.after(500,app.google_sign_in)
        else:
            cmd={"browser":"browser_login","test":"test","open":"browser_open","logout":"google_logout"}[kind]
            app.busy=True;app.commands.put((cmd,None))
    for label,kind in (("Google 로그인","browser"),("연결 확인","test"),("시트 열기","open")):
        ttk.Button(row,text=label,command=lambda k=kind:action(k)).pack(side="left",padx=(0,8))
    bottom=ttk.Frame(outer);bottom.pack(fill="x",pady=(14,0))
    ttk.Button(bottom,text="업데이트 확인",command=lambda:action("update")).pack(side="left")
    def save():
        if not app.workflow.available():return
        persist();win.destroy();app.status.set("설정을 저장했습니다.")
    ttk.Button(bottom,text="설정 저장",command=save).pack(side="right")
    ttk.Button(bottom,text="닫기",command=win.destroy).pack(side="right",padx=8)
