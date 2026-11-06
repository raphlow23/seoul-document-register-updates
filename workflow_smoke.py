"""Exercise the real native guided screens and serial worker against temporary local fixtures."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import os
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
        os.utime(files[0]['path'],ns=(1800000200000000000,1800000200000000000))
        os.utime(files[1]['path'],ns=(1800000100000000000,1800000100000000000))
        jobs=[{"hash":f["hash"],"files":[f],"title":f"원래 제목 {i}","category":"중앙센터 공문","state":"확인 필요","reason":"시험", "date":"2026-11-02","recipient":"담당자","issuer":"중앙센터","text":"2. 시험 본문\n붙임 1. 자료. 끝."} for i,f in enumerate(files)]
        store=Store(base/"history.sqlite")
        for job in jobs:store.put(job)
        store.db.close()
        from file_drop import create_root
        root=create_root();root.withdraw();errors=[];writes=[];fail_receipt=[True]
        app=App(root,base,smoke=True);flow=app.workflow
        assert app.tree.get_children('')[0]==h0, 'Newest modification must be first'
        assert app.file_drop is not None
        app.config.update(root=str(shared),downloads=str(downloads),recipient="담당자")
        app.commands.put(("config",app.config.copy()))
        def wait(predicate):
            end=time.monotonic()+15
            while not predicate():
                root.update()
                if time.monotonic()>end:raise AssertionError(f"Timed out: {app.status.get()} / {errors}")
                time.sleep(.015)
        def idle():wait(lambda:not app.busy and not app.worker.working.is_set() and app.commands.empty())
        def check_actions():
            root.deiconify();root.geometry("1000x660");root.update_idletasks()
            for label in flow.status_labels:
                assert label.winfo_height()>=label.winfo_reqheight(), 'Status text is vertically clipped'
                assert label.winfo_rooty()+label.winfo_height()<=root.winfo_rooty()+root.winfo_height()-16, 'Status footer has no bottom clearance'
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
            with patch('workflow_ui.os.startfile'),patch("app.messagebox.showerror",side_effect=lambda *a,**kw:errors.append(a)),patch("app.process",side_effect=process),patch("hwp_documents.check_template"),patch.object(flow,"export_registered") as open_hwp:
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
                flow.back();assert flow.step==2
                title=app.title_dialog
                wait(lambda:app.busy);idle()
                title.deliver({"request":title.request,"owner":title.owner,"kind":"prepare","agency":"central","text":"시험 제목","image":None,"options":[{"mode":"header","label":"제목란","title":"다른 추천"}]})
                assert title.title.get("1.0","end").strip()=="확정 제목"
                title.apply();wait(lambda:getattr(app,"route_dialog",None) is not None and bool(app.route_dialog.records));idle()
                route=app.route_dialog
                assert "0.중앙센터 공문" in route.selected and not route.children["outgoing"].get()
                flow.continue_outgoing.set(True);route.apply(True)
                wait(lambda:bool(errors));idle()
                assert flow.step==3 and flow.pages["route"].winfo_children(), "Failure must leave usable retry controls"
                assert writes==[], "Failed receipt must not silently create an outgoing record"
                errors.clear();app.send("process",[app.jobs[h0]])
                wait(lambda:flow.step==4);idle()
                assert writes==["receipt"] and app.jobs[h0]["number"]=="581"
                assert not app.tree.exists(h0) and flow.history_tree.exists(h0)
                # Reproduce the reported Back -> settings bug after a receipt has committed.
                with patch.object(app,'settings',side_effect=AssertionError('Back must never open settings')),patch('file_rollback.has_files',return_value=False):
                    flow.back();assert flow.section=='receipt' and flow.step==3
                    check_actions();flow.back();assert flow.step==2
                    check_actions();flow.back();assert flow.step==1
                assert writes==['receipt'] and app.jobs[h0]['number']=='581'
                import hwp_documents as docs
                extra={**docs.style("1"),"id":"user_fixture","label":"사용 중 추가한 양식","file":"missing-preview.hwp","blank_body":True}
                with patch.object(docs,"catalog",return_value=[*docs.catalog(),extra]):
                    flow.start_outgoing(app.jobs[h0]);root.update_idletasks()
                    def find_picker(widget):
                        if isinstance(widget,ttk.Treeview):return widget
                        for child in widget.winfo_children():
                            result=find_picker(child)
                            if result is not None:return result
                    picker=find_picker(flow.pages["outgoing_types"])
                    assert picker.exists("user_fixture")
                    picker.selection_set("user_fixture");picker.event_generate("<<TreeviewSelect>>")
                    root.update();assert flow.style_choice.get()=="user_fixture"
                flow.start_outgoing(app.jobs[h0]);assert flow.step==2
                flow.outgoing_form(app.jobs[h0],"5");editor=app.outgoing_editor
                check_actions()
                assert editor.current()["ledger_type"]=="처리지침"
                assert editor.stamp["department"].get()=="편의증진부" and editor.stamp["person"].get()==""
                editor.vars["title"].set("수정한 발신 제목");assert not editor.same.get()
                editor.vars["receiver"].set("시험 수신 기관")
                editor.vars["sender"].set("발신 담당자");assert editor.stamp["applicant"].get()=="발신 담당자"
                flow.back();assert flow.step==2
                flow.outgoing_form(app.jobs[h0],"5");editor=app.outgoing_editor
                assert editor.vars["title"].get()=="수정한 발신 제목"
                # Navigation retains in-progress edits; it never clears registration history.
                flow.history_home();flow.outgoing_form(app.jobs[h0])
                editor=app.outgoing_editor;assert editor.vars["title"].get()=="수정한 발신 제목"
                with patch("hwp_documents.check_template",side_effect=ValueError("fixture: missing form")):
                    editor.register()
                assert errors and writes==["receipt"] and flow.pending_register is None
                errors.clear()
                casebase=shared/"발신 기존 폴더";casebase.mkdir();(casebase/"012. 이전 제목").mkdir()
                editor.vars["save_folder"].set(str(casebase));editor.configure_folders()
                wait(lambda:getattr(app,"outgoing_folder_dialog",None) is not None and app.outgoing_folder_dialog.record is not None);idle()
                case=app.outgoing_folder_dialog
                assert "013." in case.preview.get("1.0","end")
                case.children["outgoing"].set(False);case.children["incoming"].set(True);case.children["reply"].set(True)
                case.save_in.set("reply");case.redraw()
                assert "2.중앙회신" in case.preview.get("1.0","end")
                case.apply();editor.save();idle()
                assert "outgoing:5" in app.config["folder_profiles"]
                emptybase=shared/"변경한 빈 저장 위치";emptybase.mkdir()
                editor.vars["save_folder"].set(str(emptybase));editor.configure_folders()
                wait(lambda:getattr(app,"outgoing_folder_dialog",None) is not None and app.outgoing_folder_dialog.record is not None);idle()
                case=app.outgoing_folder_dialog
                assert "001." in case.preview.get("1.0","end")
                assert case.children["reply"].get() and not case.children["outgoing"].get()
                case.apply()
                editor.register();wait(lambda:flow.section=="outgoing" and flow.step==4);idle()
                wait(lambda:open_hwp.called)
                opened=open_hwp.call_args.args[0]
                assert opened["outgoing"]["number"]=="12" and opened["outgoing"]["stamp"]["complete"]
                assert writes==["receipt","outgoing"],writes
                assert app.jobs[h0]["outgoing"]["stamp"]["complete"]
                with patch.object(app,'settings',side_effect=AssertionError('Back must never open settings')),patch('file_rollback.has_files',return_value=False):
                    flow.back();assert flow.step==3
                    flow.back();assert flow.step==2
                    check_actions();flow.back();assert flow.step==1
                assert writes==['receipt','outgoing'] and app.jobs[h0]['outgoing']['number']=='12'
                assert "012." in __import__("hwp_documents").filename(__import__("hwp_documents").style("5"),app.jobs[h0]["outgoing"])
                # Deleting/moving downloads removes pending items, while registered history survives.
                for f in files:Path(f["path"]).unlink()
                app.render(app.jobs[h0]);assert flow.history_tree.exists(h0)
                # Removing history rows is a persisted display preference only.
                extra_history={**deepcopy(app.jobs[h0]),'hash':'history-fixture-2'}
                app.commands.put(('save',extra_history));app.render(extra_history);idle()
                flow.history_home();flow.history_tree.selection_set([h0,'history-fixture-2'])
                app.hide_history()
                wait(lambda:not flow.history_tree.exists(h0) and not flow.history_tree.exists('history-fixture-2'));idle()
                assert flow.outgoing_list_tree.exists(h0) and flow.outgoing_list_tree.exists('history-fixture-2')
                with __import__('contextlib').closing(Store(base/'history.sqlite').db) as db:
                    import json
                    rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs')]
                    assert all(next(row for row in rows if row['hash']==key)['history_hidden'] for key in (h0,'history-fixture-2'))
                assert writes==['receipt','outgoing'] and app.jobs[h0]['number']=='581' and app.jobs[h0]['outgoing']['number']=='12'
                # Cancelled outgoing progress must not survive either list action.
                flow.outgoing_home();flow.outgoing_list_tree.selection_set(h0)
                previous_id=app.jobs[h0]['outgoing']['id'];flow.drafts[h0]={'number':'12'}
                with patch('workflow_ui.messagebox.askyesno',return_value=False):flow.restart_outgoing()
                assert app.jobs[h0]['outgoing']['id']==previous_id
                with patch('workflow_ui.messagebox.askyesno',return_value=True):flow.restart_outgoing()
                wait(lambda:flow.section=='outgoing' and flow.step==2);idle()
                assert app.jobs[h0]['outgoing']['id']!=previous_id and 'number' not in app.jobs[h0]['outgoing']
                assert h0 not in flow.drafts and app.jobs[h0]['number']=='581' and writes==['receipt','outgoing']
                check_actions();flow.outgoing_home();flow.outgoing_list_tree.selection_set(h0)
                with patch('workflow_ui.messagebox.askyesno',return_value=True):flow.restart_outgoing(hide=True)
                wait(lambda:not flow.outgoing_list_tree.exists(h0));idle()
                assert app.jobs[h0]['receipt_done'] and app.jobs[h0]['outgoing_list_hidden'] and app.jobs[h0]['number']=='581'
                flow.start_outgoing(app.jobs[h0]);assert flow.step==2 and 'number' not in app.jobs[h0]['outgoing']
                pending={**deepcopy(jobs[1]),"hash":"missing","files":[{"hash":"missing","path":str(downloads/"gone.pdf")}],"state":"확인 필요"}
                app.render(pending);assert not app.tree.exists("missing")
                assert not errors,errors
                from settings_ui import open_settings
                open_settings(app)
                for child in root.winfo_children():
                    if isinstance(child,tk.Toplevel):child.destroy()
                from outgoing_editor import folder_settings_dialog
                manager=folder_settings_dialog(app,root);root.update_idletasks()
                assert '8' in manager.template_fields, 'Relief form must appear in settings'
                manager.destroy()
                from template_manager import open_add_template
                dialog=open_add_template(app);root.update_idletasks();dialog.destroy()
                # The frozen application must include the real custom-folder dialog.
                from child_folder_editor import ChildFolderEditor
                child_results=[]
                children=ChildFolderEditor(root,'중앙센터 공문',['incoming','outgoing','reply'],['incoming','reply'],lambda order,selected:child_results.append((order,selected)))
                with patch('child_folder_editor.simpledialog.askstring',return_value='검토자료'):children.add()
                children.move(-1);children.move(-1);children.move(-1);children.apply()
                assert child_results[0][0][0]=='custom:검토자료' and 'outgoing' not in child_results[0][1]
                # Ignore pre-existing ZIPs; only a ZIP saved after watching appears.
                import zipfile
                old_archive=downloads/'old-before-watch.ZIP'
                with zipfile.ZipFile(old_archive,'w') as zipped:zipped.writestr('옛날자료.csv',b'old table')
                app.send('watch',True);idle()
                assert str(old_archive) in app.worker.seen
                assert not any(j.get('source_archive')==str(old_archive) for j in app.jobs.values())
                archive=downloads/'new-after-watch.ZIP'
                with zipfile.ZipFile(archive,'w') as zipped:
                    zipped.writestr('붙임.csv',b'fixture table');zipped.writestr('도면.dwg',b'fixture drawing');zipped.writestr('other.bin',b'keep inside zip')
                wait(lambda:any(j.get('source_archive')==str(archive) for j in app.jobs.values()));idle()
                zip_job=next(j for j in app.jobs.values() if j.get('source_archive')==str(archive) and not j.get('parent'))
                assert app.tree.exists(zip_job['hash']) and '[ZIP] new-after-watch.ZIP' in app.tree.item(zip_job['hash'],'values')[2]
                app.send('watch',False);idle()
                from core import copy_files
                zip_saved=base/'zip-copy-fixture';zip_saved.mkdir()
                copied=copy_files(zip_job,zip_saved,subfolders=['.'])
                saved_archive=next(Path(p) for p in copied if Path(p).suffix.lower()=='.zip')
                assert saved_archive.read_bytes()==archive.read_bytes()
                with zipfile.ZipFile(saved_archive) as saved:assert saved.read('other.bin')==b'keep inside zip'
                # Original PDF and automatic 2-to-attachments review use the real worker/UI.
                from pypdf import PdfWriter
                original=base/'source-preview.pdf';writer=PdfWriter();writer.add_blank_page(width=72,height=72)
                with original.open('wb') as stream:writer.write(stream)
                original_bytes=original.read_bytes()
                from ocr_text_editor import OcrTextEditor
                from ocr_excerpt import clean
                source_text='제목 원본 공문\n1. 인사말\n2. 검토할 본문입니다.\n붙임 1. 확인서 1부. 끝.\n시행 1234'
                # Spacing engine itself is exercised against the real model in unit checks.
                with patch('ocr_excerpt.clean',side_effect=lambda text,config,start,attachments,spacing:clean(text,config,start,attachments,False)):
                    review=OcrTextEditor(app,source_text,source_file={'path':str(original),'hash':digest(original)})
                    wait(lambda:review.ready and review.original.image is not None);idle()
                    assert review.result.get('1.0','end').strip().startswith('2.')
                    assert review.result.get('1.0','end').strip().endswith('끝.')
                    assert review.source.get('1.0','end').strip()==source_text
                    assert original.read_bytes()==original_bytes
                    review.close()
        finally:
            app.commands.put(("quit",None));app.worker.join(timeout=5)
            for token in root.tk.call('after','info'):root.after_cancel(token)
            root.destroy()
    from rollback_smoke import run as rollback_check
    rollback_check()
    print("Guided receipt, grouped attachments, independent numbering, history, outgoing + stamp, settings: OK")


if __name__=="__main__":run()
