from copy import deepcopy
from pathlib import Path
import queue,tempfile,tkinter as tk,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import core,outgoing
from outgoing_new import create
from outgoing_folders import prepare
from workflow_ui import SimpleOutgoing


class OutgoingOptionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.root=tk.Tk();self.root.geometry('1000x660');self.addCleanup(self.close)
        from modern_ui import apply_theme
        apply_theme(self.root)
        self.config={'root':str(self.base),'recipient':'시험 담당','outgoing_default_titles':{'one':'기본 회신(주소)','two':'다른 기본 제목'}}
        self.app=SimpleNamespace(root=self.root,data=self.base,config=self.config,commands=queue.Queue(),persist_config=Mock(),send=Mock(return_value=True),workflow=SimpleNamespace(available=lambda:True,back=Mock(),export_registered=Mock()))
        self.items=[{'id':key,'label':key,'family':'공문','folder':'.','suffix':'','prefix':'','receiver':'','default_receiver':'시험 기관','file':'fixture.hwp','blank_body':True} for key in ('one','two')]
        import hwp_documents as docs
        for name,value in (('catalog',self.items),('template_body',''),('destination',str(self.base)),('check_template',self.base/'fixture.hwp')):
            p=patch.object(docs,name,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch.object(docs,'style',side_effect=lambda key:next(x for x in self.items if x['id']==key));p.start();self.addCleanup(p.stop)

    def close(self):
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def editor(self,job=None,style='one'):
        job=job or create(self.config,self.store)
        return SimpleOutgoing(self.app,job,self.root,style)

    def test_direct_choice_never_opens_case_dialog_or_reuses_stale_plan(self):
        editor=self.editor();self.assertEqual(editor.folder_mode,'direct')
        editor.folder_plan={'base':str(self.base),'created':True,'folder_name':'old'}
        editor.vars['save_folder'].set(str(self.base));self.assertEqual(editor.folder_mode,'direct')
        before=set(self.base.iterdir())
        with patch.object(editor,'configure_folders') as chooser:editor.register()
        chooser.assert_not_called();out=self.app.send.call_args.args[1]['outgoing']
        self.assertEqual(out['folder_mode'],'direct');self.assertIsNone(out['folder_plan'])
        self.assertEqual(set(self.base.iterdir()),before)

    def test_case_choice_requests_configuration_and_unchecking_returns_to_direct(self):
        editor=self.editor();editor.storage_mode.set('case');editor.change_storage_mode()
        with patch.object(editor,'configure_folders') as chooser:editor.register()
        chooser.assert_called_once_with(resume=True);self.app.send.assert_not_called()
        with patch('outgoing_folder_chooser.OutgoingFolderChooser',side_effect=lambda app,job,out,apply:apply(None)):
            editor.configure_folders()
        self.assertEqual(editor.storage_mode.get(),'direct');self.assertIsNone(editor.folder_plan)
        with patch('outgoing_folder_chooser.OutgoingFolderChooser') as chooser:editor.register()
        chooser.assert_not_called();self.assertEqual(self.app.send.call_args.args[1]['outgoing']['folder_mode'],'direct')

    def test_per_template_titles_start_new_drafts_but_preserve_edits_and_registered_titles(self):
        editor=self.editor();self.assertEqual(editor.vars['title'].get(),'기본 회신(주소)')
        editor.vars['title'].set('회신(중구 주소)');editor.save_default_title()
        self.assertEqual(self.config['outgoing_default_titles']['one'],'회신(중구 주소)');self.app.persist_config.assert_called_once()
        self.assertEqual(self.config['outgoing_default_titles']['two'],'다른 기본 제목')
        edited=editor.current();editor.win.destroy()
        other=self.editor();self.assertEqual(other.vars['title'].get(),'회신(중구 주소)');other.win.destroy()
        job=create(self.config,self.store);job['outgoing']=edited;job['outgoing']['title']='수정 중인 제목'
        restored=self.editor(job);self.assertEqual(restored.vars['title'].get(),'수정 중인 제목');restored.win.destroy()
        job['outgoing'].update(sheet_attempted=True,title='이미 등록한 제목')
        registered=self.editor(job);registered.load_default_title();self.assertEqual(registered.vars['title'].get(),'이미 등록한 제목')

    def test_settings_store_template_titles_and_keep_them_across_panel_reload(self):
        from outgoing_editor import folder_settings_dialog
        win=folder_settings_dialog(self.app,self.root);self.root.update()
        win.template_title_fields['one'].set('설정에서 고친 제목')
        body=win.template_panel.body
        button=next(x for x in body.winfo_children() if x.winfo_class()=='TButton' and x.cget('text')=='이름 변경')
        with patch('outgoing_editor.simpledialog.askstring',return_value=None):button.invoke()
        self.assertEqual(win.template_title_fields['one'].get(),'설정에서 고친 제목')
        footer=next(x for x in win.winfo_children() if x.winfo_class()=='TFrame')
        next(x for x in footer.winfo_children() if x.winfo_class()=='TButton' and x.cget('text')=='기본 설정 저장').invoke()
        self.assertEqual(self.config['outgoing_default_titles']['one'],'설정에서 고친 제목')

    def test_direct_mode_detaches_created_plan_without_deleting_existing_folder_or_number(self):
        old=self.base/'old case';old.mkdir();(old/'existing.txt').write_text('keep')
        job=create(self.config,self.store);job['outgoing'].update(number='12',document_number='서울센터2026-012',complete=True,folder_plan={'created':True,'folder_name':'old case','base':str(self.base),'save_path':str(old)})
        edit={**job['outgoing'],'folder_mode':'direct','save_folder':str(self.base)}
        outgoing.merge_edit(job,edit)
        self.assertIsNone(job['outgoing']['folder_plan']);self.assertEqual(job['outgoing']['number'],'12')
        self.assertEqual(prepare(job,self.config,self.store),self.base);self.assertEqual((old/'existing.txt').read_text(),'keep')

    def test_registered_open_persists_new_storage_choice_before_export(self):
        editor=self.editor();editor.out.update(complete=True,number='12',document_number='서울센터2026-012')
        editor.out['stamp']['complete']=True
        editor.folder_plan={'created':True,'folder_name':'old case','base':str(self.base)}
        editor.storage_mode.set('direct');editor.change_storage_mode();editor.open_registered()
        out=self.app.send.call_args.args[1]['outgoing']
        self.assertEqual(out['folder_mode'],'direct');self.assertIsNone(out['folder_plan'])
        self.assertEqual(self.app.workflow.pending_open,editor.job['hash']);self.assertIsNone(self.app.workflow.pending_register)
        self.app.workflow.export_registered.assert_not_called()

    def test_worker_direct_export_ignores_stale_case_plan_and_keeps_actual_number(self):
        from app import Worker
        job=create(self.config,self.store);job['outgoing'].update(title='새 공문',title_same=False,template_id='one',ledger_type='공문',number='12',document_number='서울센터2026-012',complete=True,receiver='기관',sender='담당',folder_mode='direct',save_folder=str(self.base),folder_plan={'created':True,'folder_name':'unwanted','base':str(self.base)})
        self.store.put(job);commands,events=queue.Queue(),queue.Queue();worker=Worker(commands,events,self.config,self.base/'history.sqlite')
        paths=[]
        def export(item,out,path):paths.append(Path(path));Path(path).write_bytes(b'HWP fixture')
        with patch('hwp_documents.export_hwp',side_effect=export),patch('outgoing_folders.prepare',side_effect=AssertionError('Must not create a case folder')):
            worker.start();commands.put(('draft_export',{'hash':job['hash'],'outgoing':deepcopy(job['outgoing']),'path':str(self.base/'wrong.hwp'),'native_hwp':True,'copy_only':False}));commands.put(('quit',None));worker.join(timeout=8)
        self.assertFalse(worker.is_alive());self.assertEqual(len(paths),1);self.assertEqual(paths[0].parent,self.base)
        self.assertTrue(paths[0].name.startswith('012.'));self.assertFalse((self.base/'unwanted').exists())
        self.assertIsNone(self.store.get(job['hash'])['outgoing']['folder_plan'])


if __name__=='__main__':unittest.main()
