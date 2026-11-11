"""Real local file rollback + attachment GUI flow with all Google services replaced."""
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import core
from file_drop import create_root
from app import App
from folder_naming import AGENCIES


def run():
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary).resolve();shared=base/'shared';shared.mkdir()
        for name in AGENCIES['중앙센터 공문']:(shared/name).mkdir()
        source=base/'original.pdf';source.write_bytes(b'original fixture')
        config={'root':str(shared),'recipient':'담당자','downloads':str(base),'_guided_ui':True,'_program_dir':str(base/'Program')}
        key=core.digest(source)
        job={'hash':key,'id':'rollback-fixture','state':'확인 필요','files':[{'path':str(source),'hash':key}],'text':'시험 공문 본문','title':'기존 접수 제목','category':'중앙센터 공문','receipt_type':'공문','recipient':'담당자','date':'2026-11-03','issuer':'중앙센터','routing':{'agency':'중앙센터 공문','targets':[{'relative':'0.중앙센터 공문','template':'{number:03d}. {title}'}],'children':['incoming','outgoing','reply']}}
        class Forbidden:
            def __init__(self,*a,**kw):raise AssertionError('File rollback/resave must never access a ledger')
        class InitialSheet:
            def __init__(self,*a,**kw):pass
            def register(self,job,store):job['sheet_attempted']=True;store.put(job);return 6,'581'
        store=core.Store(base/'history.sqlite')
        core.process(job,config,store,sheets_factory=InitialSheet)
        folder=Path(job['storage_roots'][0]['path']);store.db.close()
        root=create_root();root.withdraw();errors=[]
        app=App(root,base,smoke=True);app.config.update(config);flow=app.workflow
        app.commands.put(('config',app.config.copy()))
        def wait(condition):
            end=time.monotonic()+15
            while not condition():
                root.update();time.sleep(.01)
                if errors:raise AssertionError(errors)
                if time.monotonic()>end:raise AssertionError('Rollback screen timed out: '+app.status.get())
        def idle():wait(lambda:not app.busy and not app.worker.working.is_set() and app.commands.empty())
        def local_process(job,config,store):return core.process(job,config,store,sheets_factory=Forbidden,outgoing_factory=Forbidden,stamp_factory=Forbidden)
        try:
            with patch('workflow_ui.os.startfile'),patch('app.messagebox.showerror',side_effect=lambda *a,**k:errors.append(a)),patch('app.process',side_effect=local_process),patch.object(app,'settings',side_effect=AssertionError('Back opened settings')):
                wait(lambda:key in app.jobs);idle()
                flow.completed(app.jobs[key]);app.send('file_rollback',{'hash':key,'scope':'receipt','step':4})
                wait(lambda:flow.step==3 and getattr(app,'route_dialog',None) is not None and bool(app.route_dialog.records));idle()
                assert not folder.exists() and source.exists()
                assert app.jobs[key]['number']=='581' and app.jobs[key]['receipt_done']
                assert app.tree.exists(key), 'Rolled-back case must remain available for attachment additions'
                app.route_dialog.apply(True);wait(lambda:flow.step==4);idle()
                assert folder.exists() and app.jobs[key]['number']=='581'
                # Simulate the screenshot: a new attachment selected with an already saved case.
                extra=base/'extra.hwpx'
                import zipfile
                with zipfile.ZipFile(extra,'w') as archive:
                    archive.writestr('Contents/section0.xml','<section><p><t>추가 첨부파일의 내용을 확인합니다. 새로운 시험 자료입니다.</t></p></section>')
                flow.receipt_home();app.send('add',[str(extra)]);idle();extra_key=core.digest(extra)
                excel=base/'붙임 양식.xlsx';excel.write_bytes(b'Excel attachment preserved byte for byte')
                app.send('add',[str(excel)]);idle();excel_key=core.digest(excel)
                assert app.tree.exists(excel_key)
                packet=base/'추가 첨부.zip'
                with zipfile.ZipFile(packet,'w') as archive:
                    archive.write(source,'original.pdf');archive.write(extra,'extra.hwpx')
                # Registered cases can be chosen in history via the direct attachment action.
                flow.history_home();flow.focus(key)
                with patch('workflow_ui.filedialog.askopenfilenames',return_value=(str(packet),str(excel))):flow.add_attachments()
                wait(lambda:len(app.jobs[key]['files'])==3 and flow.section=='receipt' and flow.step==2);idle()
                assert not folder.exists() and app.jobs[key]['number']=='581'
                assert not app.tree.exists(extra_key)
                assert not app.tree.exists(excel_key)
                flow.resume_receipt(app.jobs[key])
                wait(lambda:getattr(app,'route_dialog',None) is not None and bool(app.route_dialog.records));idle()
                app.route_dialog.apply(True);wait(lambda:flow.step==4);idle()
                assert len(app.jobs[key]['destinations'])==4 and app.jobs[key]['number']=='581'
                saved_zip=next(Path(p) for p in app.jobs[key]['destinations'] if Path(p).suffix.lower()=='.zip')
                assert saved_zip.read_bytes()==packet.read_bytes()
                assert next(Path(p) for p in app.jobs[key]['destinations'] if p.endswith('.xlsx')).read_bytes()==excel.read_bytes()
                assert source.read_bytes()==b'original fixture'
                # Continuing outgoing uses the existing receipt's send folder.
                flow.outgoing_form(app.jobs[key],'1');editor=app.outgoing_editor
                selfolder=Path(editor.vars['save_folder'].get())
                assert selfolder==folder/'2.센터발송' and selfolder.is_dir()
                assert editor.folder_mode=='direct' and editor.current()['folder_plan'] is None
                flow.outgoing_home();flow.drafts.pop(key,None);flow.focus(key)
                # A real generated output is removed independently of receipt copies.
                saved=app.jobs[key]
                from outgoing import defaults
                from outgoing_folders import prepare
                from file_rollback import track_file
                out=defaults(saved,app.config);out.update(enabled=True,number='12',complete=True,sheet_attempted=True,receiver='수신 기관',template_id='1')
                out['stamp'].update(complete=True,sheet_attempted=True,number='7')
                parent=shared/'시행문';parent.mkdir()
                out['folder_plan']={'base':str(parent),'template':'{number:03d}. {title}','folder_title':'발신 파일','date':'2026-11-03','agency':'중앙센터 공문','children':['outgoing','reply'],'save_in':'outgoing'}
                saved['outgoing']=out
                db=core.Store(base/'history.sqlite');output=prepare(saved,app.config,db)/'012. fixture.hwp';output.write_bytes(b'generated output fixture')
                out.update(hwp_path=str(output),hwp_saved_number='12',hwp_created_sha256=core.digest(output))
                track_file(saved,output,core.digest(output),'outgoing');db.put(saved);db.db.close()
                output.write_bytes(b'user saved HWP edits after export')
                # Back cancels owned outgoing/stamp cells as well as generated output.
                # All spreadsheet operations below use private in-memory fixture rows.
                from browser_journal import Journal
                from registration_cancel import markers
                from types import SimpleNamespace
                import re
                rows={}
                for scope in ('outgoing','stamp'):
                    before=['']*11;before[0]='7';after=before.copy()
                    if scope=='outgoing':before[5:7]=['서울센터2026-','12'];after=before.copy();after[1]='2026-11-03';after[2]='○';after[7:]=['발신 파일','수신 기관','담당자','Email']
                    else:after[1:]=['2026-11-03','○','','','서울센터2026-12','발신 파일','수신 기관','편의증진부','담당자','']
                    rows[scope]=[['']*11 for _ in range(5)]+[after.copy()]
                    Journal(app.config).archive(None,{'sid':'fixture-sheet','tab':scope,'row':6,'before':before,'after':after,'markers':markers(saved,scope),'complete':True})
                def fixture_owner(config,target):
                    data=rows[target['scope']]
                    def write(cell,value):data[int(cell[1:])-1][ord(cell[0])-65]=value
                    browser=SimpleNamespace(ui=SimpleNamespace(write=write),getrows=lambda address:[data[int(re.search(r'\d+',address).group())-1].copy()],close=lambda:None)
                    return SimpleNamespace(snapshot=lambda:(None,data,{}),header_row=2,last_column='K',browser=browser)
                app.render(saved);flow.outgoing_form(saved)
                with patch('registration_cancel.owner',side_effect=fixture_owner):
                    flow.back()
                    wait(lambda:flow.section=='outgoing' and flow.step==2 and not output.exists());idle()
                assert all(Path(p).is_file() for p in app.jobs[key]['destinations'])
                assert 'number' not in app.jobs[key]['outgoing'] and not app.jobs[key]['outgoing']['stamp'].get('complete')
                assert app.jobs[key]['number']=='581' and rows['outgoing'][5][7]=='' and rows['stamp'][5][6]==''
                edited=app.jobs[key]['edited_hwp_backups'][-1]
                assert Path(edited['backup']).read_bytes()==b'user saved HWP edits after export'
                assert not errors
        finally:
            flow.clear_editor();app.commands.put(('quit',None));app.worker.join(timeout=5)
            for token in root.tk.call('after','info'):root.after_cancel(token)
            root.destroy()
    print('Real file rollback, same-number file-only resave, attachments and Back cancellation of owned fixture ledger cells: OK')
