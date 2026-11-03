"""Native file drops always copy into the existing import workflow; never move originals."""
from pathlib import Path
import os
from tkinterdnd2 import TkinterDnD,DND_FILES,COPY,REFUSE_DROP

EXTENSIONS={".pdf",".hwp",".hwpx",".zip"}


def create_root():
    return TkinterDnD.Tk()


def parse_files(tk,data):
    paths=[];seen=set();skipped=0
    for value in tk.splitlist(data):
        path=Path(value)
        if path.suffix.lower() not in EXTENSIONS or not path.is_file():skipped+=1;continue
        key=os.path.normcase(os.path.abspath(path))
        if key not in seen:paths.append(str(path));seen.add(key)
    return paths,skipped


class FileDrop:
    def __init__(self,app,targets):
        self.app=app;self.targets=[]
        for target in dict.fromkeys(targets):
            target.drop_target_register(DND_FILES)
            target.dnd_bind("<<Drop>>",self.receive)
            self.targets.append(target)

    def receive(self,event):
        app=self.app
        if app.busy or app.updating or app.startup_gate or getattr(app,"closing",False) or app.worker.working.is_set():
            app.status.set("현재 작업이 끝난 뒤 파일을 다시 끌어다 놓아 주세요.")
            return REFUSE_DROP
        try:paths,skipped=parse_files(app.root.tk,event.data)
        except (ValueError,TypeError):return REFUSE_DROP
        if not paths:
            app.status.set("PDF·HWP·HWPX·ZIP 파일을 끌어다 놓으세요. 폴더는 파일 추가 대상이 아닙니다.")
            return REFUSE_DROP
        if not app.send("add",paths):return REFUSE_DROP
        app.status.set(f"파일 {len(paths)}개를 추가하는 중…"+(f" 지원하지 않는 항목 {skipped}개 제외" if skipped else ""))
        return COPY
