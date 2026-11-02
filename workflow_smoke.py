"""Exercise the real native guided screens and serial worker against temporary local fixtures."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from PIL import Image
from app import App
from core import Store, digest
from folder_naming import AGENCIES


def run():
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);downloads=base/"Downloads";downloads.mkdir()
        shared=base/"shared";shared.mkdir()
        for paths in AGENCIES.values():
            for path in paths:(shared/path).mkdir(exist_ok=True)
        (shared/"0.중앙센터 공문"/"004. 지난 공문").mkdir()
        files=[]
        for i in range(2):
            source=downloads/f"attachment{i}.pdf";source.write_bytes(f"fixture {i}, never OCRed".encode())
            files.append({"hash":digest(source),"path":str(source)})
        h0,h1=[f["hash"] for f in files]
        jobs=[{"hash":f["hash"],"files":[f],"title":f"원래 제목 {i}","category":"중앙센터 공문","state":"확인 필요","reason":"시험", "date":"2026-11-02","recipient":"담당자","issuer":"중앙센터","text":"2. 시험 본문\n붙임 1. 자료. 끝."} for i,f in enumerate(files)]
        store=Store(base/"history.sqlite")
        for job in jobs:store.put(job)
        store.db.close()
        root=tk.Tk();root.withdraw();errors=[];writes=[];fail_receipt=[True]
        app=App(root,base,smoke=True);flow=app.workflow
        app.config.update(root=str(shared),downloads=str(downloads),recipient="담당자")
        app.commands.put(("config",app.config.copy()))
        def wait(predicate):
            end=time.monotonic()+8
            while not predicate():
                root.update()
                if time.monotonic()>end:raise AssertionError(f"Timed out: {app.status.get()} / {errors}")
                time.sleep(.015)
        def idle():wait(lambda:not app.busy and not app.worker.working.is_set() and app.commands.empty())
        def check_actions():
            root.deiconify();root.geometry("1000x660");root.update_idletasks()
            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child,ttk.Button) and child.winfo_ismapped():
                        ancestor=child.master;scrollable=False
                        while ancestor is not root:
                            scrollable=scrollable or isinstance(ancestor,tk.Canvas)
                            ancestor=ancestor.master
                        if not scrollable:
                            assert child.winfo_rooty()+child.winfo_height()<=root.winfo_rooty()+root.winfo_height(),child.cget("text")
                            assert child.winfo_rootx()+child.winfo_width()<=root.winfo_rootx()+root.winfo_width(),child.cget("text")
                    walk(child)
            walk(flow.content)
            root.withdraw()
        def process(job,config,db):
            if not job.get("receipt_done") and fail_receipt[0]:
                fail_receipt[0]=False
                raise ConnectionError("fixture: connection failed before writing")
            writes.append("outgoing" if job.get("receipt_done") else "receipt")
            if not job.get("receipt_done"):
                assert not job.get("outgoing",{}).get("enabled"), "Receipt must not prematurely register outgoing"
                job.update(receipt_done=True,number="581",destinations=[str(shared/"0.중앙센터 공문"/"005. 확정 제목")])
            else:
                out=job["outgoing"];assert out["ledger_type"]=="처리지침"
                out.update(number="12",document_number="처리지침2026-012",complete=True,sheet_attempted=True)
                out["stamp"].update(complete=True,sheet_attempted=True)
            job.update(state="완료",reason="fixture registered");db.put(job);return job
        try:
            with patch("app.messagebox.showerror",side_effect=lambda *a,**kw:errors.append(a)),patch("app.process",side_effect=process):
                idle();app.tree.selection_set([h0,h1])
                flow.start_receipt();title=app.title_dialog
                assert flow.step==2 and title.embedded
                # Deliver a synthetic preview before the OCR timer. Never open a business document.
                title.deliver({"owner":title.owner,"request":title.request,"kind":"prepare","text":"제목 확정 제목", "image":Image.new("RGB",(500,700),"white"),"options":[{"mode":"header","label":"제목란","title":"확정 제목"}]})
                check_actions()
                title.apply()
                wait(lambda:getattr(app,"route_dialog",None) is not None and bool(app.route_dialog.records));idle()
                route=app.route_dialog
                check_actions()
                assert flow.current==h0 and len(app.jobs[h0]["files"])==2
                assert not app.tree.exists(h1)
                route.selected.add("0.중앙센터 공문");route.templates["0.중앙센터 공문"]="{number:03d}. {title}"
                route.children["outgoing"].set(False);route.redraw_preview()
                preview=route.preview.get("1.0","end")
                assert "005. 확정 제목" in preview and "2.중앙회신" in preview
                flow.continue_outgoing.set(True);route.apply(True)
                wait(lambda:bool(errors));idle()
                assert flow.step==3 and flow.pages["route"].winfo_children(), "Failure must leave usable retry controls"
                assert writes==[], "Failed receipt must not silently create an outgoing record"
                errors.clear();app.send("process",[app.jobs[h0]])
                wait(lambda:flow.step==4);idle()
                assert writes==["receipt"] and app.jobs[h0]["number"]=="581"
                assert not app.tree.exists(h0) and flow.history_tree.exists(h0)
                flow.start_outgoing(app.jobs[h0]);assert flow.step==2
                flow.outgoing_form(app.jobs[h0],"5");editor=app.outgoing_editor
                check_actions()
                assert editor.current()["ledger_type"]=="처리지침"
                assert editor.stamp["department"].get()=="편의증진부" and editor.stamp["person"].get()==""
                editor.vars["title"].set("수정한 발신 제목");assert not editor.same.get()
                editor.vars["receiver"].set("시험 수신 기관")
                editor.vars["sender"].set("발신 담당자");assert editor.stamp["applicant"].get()=="발신 담당자"
                # Navigation retains in-progress edits; it never clears registration history.
                flow.history_home();flow.outgoing_form(app.jobs[h0])
                editor=app.outgoing_editor;assert editor.vars["title"].get()=="수정한 발신 제목"
                editor.register();wait(lambda:flow.section=="outgoing" and flow.step==4);idle()
                assert writes==["receipt","outgoing"],writes
                assert app.jobs[h0]["outgoing"]["stamp"]["complete"]
                assert "012." in __import__("hwp_documents").filename(__import__("hwp_documents").style("5"),app.jobs[h0]["outgoing"])
                # Deleting/moving downloads removes pending items, while registered history survives.
                for f in files:Path(f["path"]).unlink()
                app.render(app.jobs[h0]);assert flow.history_tree.exists(h0)
                pending={**deepcopy(jobs[1]),"hash":"missing","files":[{"hash":"missing","path":str(downloads/"gone.pdf")}],"state":"확인 필요"}
                app.render(pending);assert not app.tree.exists("missing")
                assert not errors,errors
                from settings_ui import open_settings
                open_settings(app)
                for child in root.winfo_children():
                    if isinstance(child,tk.Toplevel):child.destroy()
        finally:
            app.commands.put(("quit",None));app.worker.join(timeout=5)
            root.destroy()
    print("Guided receipt, grouped attachments, independent numbering, history, outgoing + stamp, settings: OK")


if __name__=="__main__":run()
