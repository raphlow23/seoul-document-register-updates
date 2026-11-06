"""Use the same transparent document icon for the executable, app windows and shortcuts."""
import os
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
import uuid

APP_ID='SeoulDocumentRegister.DocumentRegistry'
DISPLAY_NAME='서울센터 공문 업무'


def asset_icon():return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'assets'/'app_icon.ico'


def persistent_icon(data):
    content=asset_icon().read_bytes();sha=hashlib.sha256(content).hexdigest()
    folder=Path(data)/'ui-assets';folder.mkdir(parents=True,exist_ok=True)
    target=folder/('document-exchange-'+sha[:16]+'.ico')
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
        temporary=folder/('icon-'+uuid.uuid4().hex+'.tmp');temporary.write_bytes(content);os.replace(temporary,target)
    return target.resolve()


def prepare_process_identity():
    if os.name=='nt':
        result=ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        if result<0:raise OSError('작업표시줄 앱 식별자를 설정하지 못했습니다.')


def repair_shortcuts(exe,ico,paths=None):
    if os.name!='nt':return {'updated':[],'errors':[]}
    import pythoncom
    from win32com.shell import shell,shellcon
    from win32com.propsys import propsys,pscon
    exe=Path(exe).resolve();result={'updated':[],'errors':[]}
    if paths is None:
        paths=[]
        for folder in (Path(shell.SHGetFolderPath(0,shellcon.CSIDL_DESKTOPDIRECTORY,None,0)),Path(shell.SHGetFolderPath(0,shellcon.CSIDL_PROGRAMS,None,0)),Path(os.environ.get('APPDATA',''))/'Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar'):
            if folder.is_dir():paths.extend(folder.glob('*.lnk'))
    # Called on the GUI/installer COM thread; do not tear down its shared apartment.
    for path in dict.fromkeys(map(Path,paths)):
        try:
            link=pythoncom.CoCreateInstance(shell.CLSID_ShellLink,None,pythoncom.CLSCTX_INPROC_SERVER,shell.IID_IShellLink)
            persist=link.QueryInterface(pythoncom.IID_IPersistFile);persist.Load(str(path),66)
            target=Path(os.path.expandvars(link.GetPath(shell.SLGP_RAWPATH)[0])).resolve()
            if os.path.normcase(str(target))!=os.path.normcase(str(exe)):continue
            link.SetIconLocation(str(ico),0)
            store=link.QueryInterface(propsys.IID_IPropertyStore)
            store.SetValue(pscon.PKEY_AppUserModel_ID,propsys.PROPVARIANTType(APP_ID));store.Commit()
            persist.Save(str(path),1)
            shell.SHChangeNotify(shellcon.SHCNE_UPDATEITEM,5,str(path),None)
            result['updated'].append(str(path))
        except Exception as error:result['errors'].append(str(error))
    return result


def prepare_desktop_identity(data,exe):
    ico=persistent_icon(data);result=repair_shortcuts(exe,ico)
    (Path(data)/'taskbar-shortcut-status.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return result


def apply_window_icon(root,data=None,exe=None):
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'assets'
    ico=base/'app_icon.ico';png=base/'app_icon.png'
    if os.name=='nt' and ico.is_file():
        ico=persistent_icon(data) if data is not None else ico.resolve()
        root.iconbitmap(default=str(ico));root.iconbitmap(str(ico))
        from win32com.propsys import propsys,pscon
        import win32gui,win32con
        hwnd=win32gui.GetAncestor(root.winfo_id(),2)
        store=propsys.SHGetPropertyStoreForWindow(hwnd)
        store.SetValue(pscon.PKEY_AppUserModel_RelaunchIconResource,propsys.PROPVARIANTType(str(ico)+',0'))
        target=Path(exe or sys.executable).resolve()
        store.SetValue(pscon.PKEY_AppUserModel_RelaunchCommand,propsys.PROPVARIANTType(subprocess.list2cmdline([str(target)])))
        store.SetValue(pscon.PKEY_AppUserModel_RelaunchDisplayNameResource,propsys.PROPVARIANTType(DISPLAY_NAME))
        store.SetValue(pscon.PKEY_AppUserModel_ID,propsys.PROPVARIANTType(APP_ID));store.Commit()
        result={'app_id':store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue(),
                'icon_resource':store.GetValue(pscon.PKEY_AppUserModel_RelaunchIconResource).GetValue(),
                'relaunch_command':store.GetValue(pscon.PKEY_AppUserModel_RelaunchCommand).GetValue(),
                'big_icon':win32gui.SendMessage(hwnd,win32con.WM_GETICON,1,0),'small_icon':win32gui.SendMessage(hwnd,win32con.WM_GETICON,0,0)}
        root._taskbar_icon_details=result
        if data is not None:(Path(data)/'taskbar-window-status.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    elif png.is_file():
        root._document_icon=tk.PhotoImage(master=root,file=str(png));root.iconphoto(True,root._document_icon)
