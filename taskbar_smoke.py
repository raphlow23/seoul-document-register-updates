"""Verify the actual native window and shortcut identities without touching the user's pins."""
import ctypes
import json
from pathlib import Path
import sys
import tkinter as tk
from app_icon import APP_ID,apply_window_icon,prepare_process_identity,persistent_icon,repair_shortcuts


def run(output):
    import pythoncom
    from win32com.shell import shell
    from win32com.propsys import propsys,pscon
    output=Path(output);data=output.parent/'taskbar-fixture';data.mkdir(parents=True,exist_ok=True)
    prepare_process_identity();root=tk.Tk();root.title('서울센터 공문 업무 - 아이콘 검증');root.geometry('500x260')
    apply_window_icon(root,data);root.update()
    details=dict(root._taskbar_icon_details)
    pointer=ctypes.c_void_p()
    result=ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(pointer))
    assert result==0
    try:details['process_app_id']=ctypes.wstring_at(pointer)
    finally:ctypes.windll.ole32.CoTaskMemFree(pointer)
    assert details['app_id']==details['process_app_id']==APP_ID and details['big_icon'] and details['small_icon']
    ico=persistent_icon(data);assert details['icon_resource']==str(ico)+',0'
    # An owned .lnk and an unrelated .lnk prove that repair does not rewrite other applications.
    own=data/'owned.lnk';other=data/'other.lnk'
    for path,target in ((own,Path(sys.executable)),(other,data/'other.exe')):
        link=pythoncom.CoCreateInstance(shell.CLSID_ShellLink,None,pythoncom.CLSCTX_INPROC_SERVER,shell.IID_IShellLink)
        link.SetPath(str(target));link.SetArguments('--fixture-argument')
        link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path),1)
    before=other.read_bytes();repaired=repair_shortcuts(sys.executable,ico,[own,other])
    assert repaired['updated']==[str(own)] and not repaired['errors'] and other.read_bytes()==before
    link=pythoncom.CoCreateInstance(shell.CLSID_ShellLink,None,pythoncom.CLSCTX_INPROC_SERVER,shell.IID_IShellLink)
    link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(own))
    assert link.QueryInterface(propsys.IID_IPropertyStore).GetValue(pscon.PKEY_AppUserModel_ID).GetValue()==APP_ID
    assert link.GetIconLocation()[0]==str(ico) and link.GetArguments()=='--fixture-argument'
    details.update(owned_shortcut_repaired=True,other_shortcut_preserved=True,shortcut_arguments_preserved=True)
    root.destroy();assert ico.is_file();details['persistent_icon_after_window_close']=True
    output.write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
    return details
