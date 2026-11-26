import os
import json
import queue
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import core
from app import Worker
from attachment_files import intake_file
from browser_journal import Journal
from registration_cancel import markers


def exercise_reopened_window(base,key,action):
    import tkinter as tk
    from tkinter import ttk
    from contextlib import ExitStack
    from app import App
    root=tk.Tk();root.withdraw()
    app=App(root,base,smoke=True)
    def button(widget,label):
        if isinstance(widget,ttk.Button) and widget.cget('text')==label:return widget
        for child in widget.winfo_children():
            match=button(child,label)
            if match is not None:return match
    try:
        with ExitStack() as patches:
            issues=[]
            patches.enter_context(patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:issues.append(a)))
            patches.enter_context(patch.object(app.workflow,'open_receipt_folders'))
            row=json.loads((base/'fake-row.json').read_text(encoding='utf-8'))
            def write(cell,value):row[ord(cell[0])-65]=value.lstrip("'")
            browser=SimpleNamespace(ui=SimpleNamespace(write=write),getrows=lambda addr:[row.copy()],close=Mock())
            patches.enter_context(patch('registration_cancel.owner',return_value=SimpleNamespace(
                snapshot=lambda:(None,[[],[],[],row.copy()],{}),header_row=2,last_column='H',browser=browser)))
            flow=app.workflow
            flow.history_home()
            flow.history_tree.selection_set(key)
            flow.review_history_selection()
            if action=='archive':
                flow.completed(app.jobs[key])
                target=button(flow.content,'저장 확인 · 원본 보관')
            else:
                target=button(flow.content,'접수 취소 · 파일 되돌리기')
            assert target is not None
            target.invoke()
            end=time.monotonic()+10
            while time.monotonic()<end:
                root.update()
                job=app.jobs[key]
                done=(job.get('receipt_originals') or {}).get('state')=='archived' if action=='archive' else not job.get('receipt_done')
                if done:break
                if issues:raise AssertionError(issues)
                time.sleep(.03)
            else:raise AssertionError((action,app.status.get(),issues))
    finally:
        app.commands.put(('quit',None));app.worker.join(5);root.destroy()


class OriginalReceiptTests(unittest.TestCase):
    def setUp(self):
        scratch=tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        base=Path(scratch.name)
        self.downloads=base/'downloads'
        self.extra=base/'extra'
        self.share=base/'share'
        self.year=Path('5.보건복지부 및 중앙처리지침')/'1. 중앙지침(처리지침, 업무연락 등)'
        for folder in (self.downloads,self.extra,self.share/'0.중앙센터 공문',self.share/self.year/'2026'):
            folder.mkdir(parents=True)
        self.config={'downloads':str(self.downloads),'root':str(self.share),'_program_dir':str(base/'Program'),
                     'recipient':'담당자','auth_mode':'browser','spreadsheet_id':'fixture-only'}
        self.database=base/'history.sqlite'
        self.store=core.Store(self.database)
        self.addCleanup(self.store.db.close)
        self.bundle=self.downloads/'원본.zip'
        with zipfile.ZipFile(self.bundle,'w') as z:z.writestr('도면.dwg',b'drawing')
        self.book=self.downloads/'붙임.xlsx'
        self.book.write_bytes(b'original sheet')
        self.job,_=core.ingest(self.bundle,self.store)
        self.job['files'].append(intake_file(self.book,core.digest(self.book)))
        self.job.update(title='시험 접수',date='2026-11-26',category='중앙센터 공문',recipient='담당자',
                        routing={'agency':'중앙센터 공문','targets':[
                            {'relative':'0.중앙센터 공문','template':'{title}','children':['incoming']},
                            {'relative':str(self.year),'template':'{title}','storage_mode':'year','year_folder':'2026','children':[]}]})
        self.store.put(self.job)
        self.existing=self.share/self.year/'2026'/self.book.name
        self.existing.write_bytes(self.book.read_bytes())
        self.payload={'hash':self.job['hash'],'scope':'receipt','expected_id':self.job['id']}
        self.row=[]
        self.start_worker()
        self.addCleanup(self.stop_worker)
        self.enterContext(patch('app.process',side_effect=lambda job,config,db:core.process(job,config,db,self.sheet)))
        self.enterContext(patch('registration_cancel.owner',side_effect=self.ledger))

    def sheet(self,config):
        def register(job,store):
            before=['1','','번호','001','','','','']
            self.row=['1',job['date'],'번호','001',job['title'],'중앙센터','담당자','Email']
            Journal(self.config).archive(None,dict(sid=config['spreadsheet_id'],tab=config['tab'],
                row=4,before=before,after=self.row.copy(),markers=markers(job,'receipt'),complete=True))
            return 4,'001'
        return SimpleNamespace(register=register)

    def ledger(self,config,target):
        def write(cell,value):self.row[ord(cell[0])-65]=value.lstrip("'")
        browser=SimpleNamespace(ui=SimpleNamespace(write=write),getrows=lambda address:[self.row.copy()],close=Mock())
        return SimpleNamespace(snapshot=lambda:(None,[[],[],[],self.row.copy()],{}),header_row=2,last_column='H',browser=browser)

    def start_worker(self):
        self.commands=queue.Queue()
        self.events=queue.Queue()
        self.worker=Worker(self.commands,self.events,self.config,self.database)
        self.worker.start()

    def stop_worker(self):
        self.commands.put(('quit',None))
        self.worker.join(10)
        self.assertFalse(self.worker.is_alive())

    def command(self,cmd,data=None,error=None):
        self.commands.put((cmd,self.payload if data is None else data))
        errors=[]
        while True:
            kind,result=self.events.get(timeout=15)
            if kind=='error':errors.append(result)
            if kind=='idle':break
        if error:self.assertIn(error,' '.join(errors))
        else:self.assertEqual(errors,[])
        return self.store.get(self.job['hash'])

    def receive(self,error=None):
        return self.command('process',[self.store.get(self.job['hash'])],error)

    def extra_copy(self,error=None):
        return self.command('additional_save',{**self.payload,'base':str(self.extra),'folder_name':''},error)

    def test_confirm_restart_cancel_preserves_existing_files_and_restores_originals(self):
        job=self.receive()
        primary=Path(job['destinations'][0])
        self.extra_copy()
        self.assertTrue(self.bundle.is_file() and self.book.is_file())
        job=self.command('receipt_archive_originals')
        self.assertFalse(self.bundle.exists() or self.book.exists())
        vault=[Path(e['backup']) for e in job['receipt_originals']['entries']]
        self.assertTrue(all(p.is_file() for p in vault))
        self.stop_worker();self.start_worker()
        self.command('receipt_archive_originals')
        cancelled=self.command('cancel_registration')
        self.assertNotIn('receipt_done',cancelled)
        self.assertTrue(self.bundle.is_file() and self.book.is_file())
        self.assertFalse(any(p.exists() for p in vault))
        self.assertTrue(self.existing.is_file())
        self.assertFalse(primary.exists() or list(self.extra.iterdir()))
        self.assertEqual(self.row[4],'')
        self.assertEqual(cancelled['source_archive'],str(self.bundle))
        self.assertEqual(len(cancelled['receipt_originals_history']),1)

    def test_interrupted_additional_copy_cancel_keeps_originals(self):
        self.receive()
        from additional_storage import _copy_one
        copies=[]
        def interrupt(*args):
            if copies:raise OSError('중단 시험')
            _copy_one(*args);copies.append(True)
        with patch('additional_storage._copy_one',side_effect=interrupt):self.extra_copy('중단 시험')
        self.command('receipt_archive_originals',error='완료 후')
        self.assertTrue(self.book.is_file() and self.bundle.is_file())
        self.stop_worker();self.start_worker()
        self.command('cancel_registration')
        self.assertEqual(list(self.extra.iterdir()),[])
        self.assertTrue(self.existing.is_file())

    def test_primary_copy_failure_keeps_sources_and_allows_cancel(self):
        from core import shutil
        original=shutil.copyfileobj
        def fail(src,dst,*args):
            if Path(src.name).name==self.book.name:
                dst.write(b'p');raise OSError('복사 실패 시험')
            return original(src,dst,*args)
        with patch('core.shutil.copyfileobj',side_effect=fail):job=self.receive('복사 실패 시험')
        self.assertFalse(job.get('receipt_done'))
        self.assertTrue(self.book.is_file() and self.bundle.is_file())
        self.command('cancel_registration')
        self.assertEqual(list((self.share/'0.중앙센터 공문').iterdir()),[])
        self.assertTrue(self.existing.is_file())

    def test_retry_after_primary_publication_reuses_only_its_own_file(self):
        from additional_storage import _publish
        def interrupt(src,dst):
            _publish(src,dst)
            raise OSError('게시 직후 중단')
        with patch('additional_storage._publish',side_effect=interrupt):
            job=self.receive('게시 직후 중단')
        target=Path(job['receipt_originals']['pending_copy']['target'])
        before=target.stat().st_ino
        self.stop_worker();self.start_worker()
        job=self.receive()
        self.assertTrue(job['receipt_done'])
        self.assertEqual(target.stat().st_ino,before)
        self.assertEqual(job['destinations'].count(str(target)),1)
        self.command('receipt_archive_originals')
        self.command('cancel_registration')
        self.assertFalse(target.exists())

    def test_same_name_and_bytes_from_another_owner_are_preserved(self):
        job=self.receive()
        target=Path(job['destinations'][0])
        replacement=target.with_suffix('.replacement')
        replacement.write_bytes(target.read_bytes())
        os.replace(replacement,target)
        self.command('receipt_archive_originals',error='변경')
        self.command('cancel_registration',error='보존')
        self.assertTrue(target.is_file() and self.book.is_file() and self.bundle.is_file())

    def test_replacement_during_rollback_is_not_removed(self):
        from program_storage import copy_verified
        job=self.receive()
        target=Path(job['destinations'][0])
        original=target.read_bytes()
        def swap_after_backup(src,dst,expected):
            copy_verified(src,dst,expected)
            if target.is_relative_to(src):
                new=target.with_suffix('.other')
                new.write_bytes(original)
                os.replace(new,target)
        with patch('program_storage.copy_verified',side_effect=swap_after_backup):
            self.command('cancel_registration',error='보존')
        self.assertEqual(target.read_bytes(),original)
        self.assertTrue(self.bundle.is_file() and self.book.is_file())

    def test_restore_name_collision_and_edited_copy_can_be_resolved_then_retried(self):
        self.receive();self.extra_copy();self.command('receipt_archive_originals')
        self.book.write_bytes(b'new owner')
        self.command('cancel_registration',error='충돌')
        self.assertEqual(self.book.read_bytes(),b'new owner')
        self.book.rename(self.downloads/'다른 사람.xlsx')
        edited=self.extra/self.book.name
        edited.write_bytes(b'edited')
        self.command('cancel_registration',error='보존')
        self.assertEqual(self.book.read_bytes(),b'original sheet')
        self.assertEqual(edited.read_bytes(),b'edited')
        edited.rename(self.extra/'수정본.xlsx')
        self.command('cancel_registration')
        self.assertTrue((self.extra/'수정본.xlsx').is_file() and self.existing.is_file())

    def test_interrupted_move_can_resume_or_cancel_after_restart(self):
        self.receive()
        from receipt_originals import move_original
        moves=[]
        def fail(entry,src,dst):
            if moves:raise OSError('이동 중단')
            move_original(entry,src,dst);moves.append(src)
        with patch('receipt_originals.move_original',side_effect=fail):
            self.command('receipt_archive_originals',error='이동 중단')
        self.stop_worker();self.start_worker()
        job=self.command('receipt_archive_originals')
        self.assertEqual(job['receipt_originals']['state'],'archived')
        self.command('cancel_registration')
        self.assertTrue(self.bundle.is_file() and self.book.is_file())

    def test_changed_or_missing_primary_and_foreign_receipt_never_deleted(self):
        job=self.receive()
        primary=Path(job['destinations'][0])
        primary.rename(primary.with_suffix('.old'))
        self.command('receipt_archive_originals',error='누락')
        self.assertTrue(self.book.is_file() and self.bundle.is_file())
        primary.with_suffix('.old').rename(primary)
        self.store.put({'id':'another','hash':'another','state':'완료','files':[],
                        'destinations':[str(primary)]})
        self.command('cancel_registration',error='다른 접수')
        self.assertTrue(primary.is_file())

    def test_confirmation_and_reversal_through_separate_windows_processes(self):
        self.receive();self.extra_copy();self.stop_worker()
        base=self.database.parent
        (base/'config.json').write_text(json.dumps(self.config),encoding='utf-8')
        (base/'fake-row.json').write_text(json.dumps(self.row),encoding='utf-8')
        script=("import site,sys;site.addsitedir('tmp/workflow-test-deps');"
                "from test_receipt_originals import exercise_reopened_window,Path;"
                "exercise_reopened_window(Path(sys.argv[1]),sys.argv[2],sys.argv[3])")
        for action in ('archive','undo'):
            subprocess.run([sys.executable,'-X','utf8','-c',script,str(base),self.job['hash'],action],
                           check=True,timeout=35,env={**os.environ,'LOCALAPPDATA':str(base)},
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(self.bundle.exists(),action=='undo')
