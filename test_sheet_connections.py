from copy import deepcopy
from pathlib import Path
import queue,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import core,outgoing
from sheet_connections import *


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.config={'root':str(self.base),'spreadsheet_id':'original-file','tab':'original-receipt','recipient':'담당자','_program_dir':str(self.base/'Program'),'auth_mode':'browser',
                     'ledger_connections':{s+':'+f:{'spreadsheet_id':s+'-'+str(i),'tab':s+'-'+f} for i,f in enumerate(FAMILIES) for s in SCOPES}}

    def job(self,family='공문'):
        job={'id':'fixture-'+family,'hash':'fixture-'+family,'title':'공문 제목','date':'2026-11-10','recipient':'담당자','issuer':'기관','category':'중앙센터 공문','state':'확인 필요','files':[],'receipt_type':family}
        job['outgoing']=outgoing.defaults(job,self.config);job['outgoing'].update(enabled=True,ledger_type=family,receiver='수신 기관')
        return job

    def test_nine_defaults_and_native_url_parsing(self):
        for family in FAMILIES:
            for scope in SCOPES:self.assertEqual(default_connection(self.config,scope,family)['tab'],scope+'-'+family)
        self.assertEqual(spreadsheet_id('https://docs.google.com/spreadsheets/d/new-file/edit?gid=123'),'new-file')
        with self.assertRaises(ValueError):spreadsheet_id('https://other.example/spreadsheets/d/file/edit')

    def test_incomplete_separate_connections_fail_before_registration(self):
        config={**self.config,'spreadsheet_id':'','ledger_connections':{'outgoing:공문':{'spreadsheet_id':'outgoing-file','tab':'발신'}}}
        job=self.job();job['receipt_done']=True
        with self.assertRaisesRegex(ValueError,'주소 또는 ID'):preflight(config,job)
        self.assertNotIn('sheet_attempted',job['outgoing'])

    def test_receipt_outgoing_and_stamp_use_independent_destinations_for_each_family(self):
        calls=[]
        class Receipt:
            def __init__(inner,config):inner.config=config
            def register(inner,job,store):calls.append(('receipt',inner.config));job['sheet_attempted']=True;return 6,'350'
        class Out:
            def __init__(inner,config):inner.config=config
            def register(inner,job,store):calls.append(('outgoing',inner.config));job['outgoing'].update(sheet_attempted=True,document_number='서울센터2026-012');return 7,'12'
        class Stamp:
            def __init__(inner,config):inner.config=config
            def register(inner,job,store):calls.append(('stamp',inner.config));job['outgoing']['stamp']['sheet_attempted']=True;return 8,'445'
        for family in FAMILIES:
            job=self.job(family);calls.clear()
            with patch('core.copy_files',return_value=[]):core.process(job,self.config,self.store,Receipt,Out,Stamp)
            self.assertEqual([s for s,c in calls],list(SCOPES))
            for scope,config in calls:
                target=default_connection(self.config,scope,family)
                self.assertEqual((config['spreadsheet_id'],config['tab']),(target['spreadsheet_id'],target['tab']))
                self.assertEqual(record(job,scope)['sheet_connection'],target)

    def test_failed_outgoing_and_stamp_retries_keep_original_files_after_defaults_change(self):
        job=self.job();job['receipt_done']=True;calls=[]
        class Out:
            def __init__(inner,config):inner.config=config
            def register(inner,job,store):
                calls.append(('outgoing',inner.config['spreadsheet_id']));job['outgoing']['sheet_attempted']=True;store.put(job)
                if len(calls)==1:raise ConnectionError('after attempted write')
                job['outgoing']['document_number']='서울센터2026-012';return 7,'12'
        class Stamp:
            def __init__(inner,config):inner.config=config
            def register(inner,job,store):calls.append(('stamp',inner.config['spreadsheet_id']));return 8,'445'
        with self.assertRaises(ConnectionError):core.process(job,self.config,self.store,outgoing_factory=Out,stamp_factory=Stamp)
        changed=deepcopy(self.config);changed['ledger_connections']={};changed['spreadsheet_id']='different-file'
        core.process(self.store.get(job['hash']),changed,self.store,outgoing_factory=Out,stamp_factory=Stamp)
        self.assertEqual(calls,[('outgoing','outgoing-0'),('outgoing','outgoing-0'),('stamp','stamp-0')])

    def test_document_override_can_change_before_attempt_but_not_after(self):
        job=self.job();target={'spreadsheet_id':'manual-file','tab':'다른 발신탭'}
        set_override(job,self.config,'outgoing',target);self.assertEqual(bind(self.config,job,'outgoing','공문')['tab'],'다른 발신탭')
        set_override(job,self.config,'outgoing',None);self.assertEqual(chosen(self.config,job,'outgoing','공문')['tab'],'outgoing-공문')
        job['outgoing']['complete']=True
        with self.assertRaisesRegex(ValueError,'이미 시작'):set_override(job,self.config,'outgoing',target)
        set_override(job,self.config,'stamp',target);self.assertEqual(chosen(self.config,job,'stamp','공문'),target)

    def test_old_completed_jobs_are_pinned_before_global_change_and_stale_edits_keep_binding(self):
        job=self.job();job.update(receipt_done=True,receipt_tab='legacy receipt');job['outgoing'].update(complete=True,tab='legacy outgoing');job['outgoing']['stamp']['complete']=True
        stale=deepcopy(job['outgoing']);self.store.put(job);updated=pin_existing(self.store,self.config);self.assertEqual(len(updated),1)
        saved=self.store.get(job['hash']);outgoing.merge_edit(saved,stale)
        self.assertEqual(saved['sheet_connection']['tab'],'legacy receipt');self.assertEqual(saved['outgoing']['sheet_connection']['tab'],'legacy outgoing')
        self.assertEqual(saved['outgoing']['stamp']['sheet_connection']['spreadsheet_id'],'stamp-0')

    def test_real_browser_ledger_constructors_keep_custom_tab_names_without_migration(self):
        from test_browser_sheets import UI
        from browser_journal import Journal
        from outgoing import OutgoingSheets
        from stamp_ledger import StampSheets
        for factory in (core.Sheets,OutgoingSheets,StampSheets):
            ui=UI([]);ui.connect=lambda:ui
            with patch('browser_sheets.BrowserUI',return_value=ui),patch.object(Journal,'migrate') as migrate:
                owner=factory({**self.config,'tab':'사용자 탭','_target_tab':'사용자 탭'})
            self.assertEqual(owner.tab,'사용자 탭');migrate.assert_not_called()

    def test_settings_and_document_dialogs_save_explicit_targets(self):
        import tkinter as tk
        from sheet_connection_ui import open_defaults,open_document
        root=tk.Tk();self.addCleanup(root.destroy)
        app=SimpleNamespace(root=root,config=deepcopy(self.config),commands=queue.Queue(),status=Mock(),persist_config=Mock(),send=Mock(return_value=True),jobs={'fixture':self.job()})
        win=open_defaults(app);self.assertEqual(len(win.connection_fields),9)
        win.connection_fields['outgoing:처리지침']['spreadsheet_id'].set('https://docs.google.com/spreadsheets/d/custom-file/edit')
        win.connection_fields['outgoing:처리지침']['tab'].set('처리지침 발신 기록')
        def button(widget,label):
            for child in widget.winfo_children():
                if child.winfo_class()=='TButton' and child.cget('text')==label:return child
                found=button(child,label)
                if found:return found
        button(win,'기본 연결 저장').invoke();self.assertEqual(app.config['ledger_connections']['outgoing:처리지침']['spreadsheet_id'],'custom-file')
        doc=open_document(app,'fixture','outgoing','공문');doc.connection_fields['spreadsheet_id'].set('document-file');doc.connection_fields['tab'].set('문서 전용 탭')
        button(doc,'이 문서에 적용').invoke();self.assertEqual(app.send.call_args.args,('sheet_override',{'hash':'fixture','scope':'outgoing','target':{'spreadsheet_id':'document-file','tab':'문서 전용 탭'}}))
        for token in root.tk.call('after','info'):root.after_cancel(token)


if __name__=='__main__':unittest.main()
