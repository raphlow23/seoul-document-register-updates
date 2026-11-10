from pathlib import Path
import json,queue,tempfile,tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import hwp_documents as docs
import template_library as library


class TemplateManagementTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name);self.root=self.base/'hwp-templates';self.root.mkdir()
        self.item={'id':'1','label':'센터발송','role':'center_send','file':'original.hwp','family':'공문','folder':'0.시행문(센터)','blank_body':True,'metadata_only':True}
        (self.root/'original.hwp').write_bytes(b'preserved original HWP')
        (self.root/'catalog.json').write_text(json.dumps([self.item]),encoding='utf-8')
        mock=patch.object(docs,'template_root',return_value=self.root);mock.start();self.addCleanup(mock.stop)
        docs.template_body.cache_clear();self.addCleanup(docs.template_body.cache_clear)

    def test_edit_copy_keeps_installed_form_unchanged_until_applied(self):
        copy=library.edit_copy('1',{'_program_dir':str(self.base/'Program')})
        self.assertIn('HWP-양식수정',str(copy));self.assertEqual(copy.read_bytes(),(self.root/'original.hwp').read_bytes())
        copy.write_bytes(b'unsaved draft edits')
        self.assertEqual((self.root/'original.hwp').read_bytes(),b'preserved original HWP')
        self.assertEqual(docs.style('1'),self.item)

    def test_edit_dialog_opens_private_hwp_and_refreshes_changes_before_apply(self):
        from template_manager import open_edit_template
        self.item.update(title_fragments=['기존 제목','(기존 주소)'],old_number='서울센터 {{발신번호}}',old_date='({{발신일}})',date_pattern='({year}. {month:02d}. {day:02d}.)',number_pattern='서울센터{year}-{number:03d}')
        (self.root/'catalog.json').write_text(json.dumps([self.item]),encoding='utf-8')
        root=tk.Tk();root.withdraw();saved=Mock()
        app=SimpleNamespace(root=root,data=self.base,config={'root':str(self.base),'_program_dir':str(self.base/'Program')},status=tk.StringVar(root),persist_config=Mock(),commands=queue.Queue())
        def buttons(widget):
            return {**({widget.cget('text'):widget} if widget.winfo_class()=='TButton' else {}),**{k:v for c in widget.winfo_children() for k,v in buttons(c).items()}}
        try:
            with patch.object(library,'inspect',return_value='기존 제목\n(기존 주소)\n서울센터 {{발신번호}}\n({{발신일}})'),patch('template_manager.os.startfile') as opened,patch('template_manager.messagebox.showinfo'):
                win=open_edit_template(app,'1',on_saved=saved);root.update();controls=buttons(win)
                path=Path(win.template_values['source'].get());self.assertNotEqual(path,self.root/'original.hwp')
                controls['한글에서 양식 수정'].invoke();opened.assert_called_once_with(str(path))
                external=self.base/'external.hwp';external.write_bytes(b'external original')
                win.template_values['source'].set(str(external));controls['한글에서 양식 수정'].invoke()
                path=Path(opened.call_args.args[0]);self.assertNotEqual(path,external)
                self.assertIn('HWP-양식수정',str(path));self.assertEqual(external.read_bytes(),b'external original')
                win.template_values['label'].set('수정한 기본 양식');path.write_bytes(b'edited HWP bytes')
                controls['수정 내용 저장'].invoke();saved.assert_not_called()
                self.assertEqual(win.template_values['label'].get(),'수정한 기본 양식')
                controls['수정 내용 저장'].invoke();saved.assert_called_once()
                item=docs.style('1');self.assertEqual(item['label'],'수정한 기본 양식');self.assertEqual((self.root/item['file']).read_bytes(),b'edited HWP bytes')
                self.assertEqual(item['title_fragments'],self.item['title_fragments']);self.assertEqual(item['date_pattern'],self.item['date_pattern']);self.assertEqual(item['number_pattern'],self.item['number_pattern'])
                self.assertEqual(app.config['outgoing_folders']['1'],item['folder']);app.persist_config.assert_called_once()
                self.assertEqual((self.root/'original.hwp').read_bytes(),b'preserved original HWP')
        finally:
            for token in root.tk.call('after','info'):root.after_cancel(token)
            root.destroy()

    def test_picker_exposes_edit_delete_and_handles_deleting_last_form(self):
        from workflow_ui import WorkflowShell
        from modern_ui import apply_theme
        root=tk.Tk();apply_theme(root);root.geometry('1000x660')
        job={'hash':'fixture','title':'','standalone_outgoing':True};page=tk.ttk.Frame(root);page.pack(fill='both',expand=True)
        app=SimpleNamespace(root=root,data=self.base,config={},status=tk.StringVar(root),jobs={'fixture':job})
        flow=WorkflowShell.__new__(WorkflowShell);flow.app=app;flow.drafts={};flow.available=lambda:True;flow.clear_editor=Mock();flow.focus=lambda key:setattr(flow,'current',key);flow.empty=lambda key:page;flow.show=Mock();flow.outgoing_home=Mock();flow.outgoing_form=Mock()
        def walk(widget):
            yield widget
            for c in widget.winfo_children():yield from walk(c)
        try:
            with patch('hwp_documents.template_preview',return_value=None):
                flow.start_outgoing(job);root.update()
                controls={x.cget('text'):x for x in walk(page) if x.winfo_class()=='TButton'}
                for x in controls.values():self.assertLessEqual(x.winfo_rootx()+x.winfo_width(),root.winfo_rootx()+root.winfo_width())
                with patch('template_manager.open_edit_template') as edit:controls['양식 수정'].invoke();self.assertEqual(edit.call_args.args[:2],(app,'1'))
                with patch('workflow_ui.messagebox.askyesno',return_value=True):
                    controls['양식 삭제'].invoke();root.update();self.assertEqual(flow.style_choice.get(),'8')
                    controls['양식 삭제'].invoke();root.update();self.assertEqual(flow.style_choice.get(),'')
                self.assertEqual(docs.style('1'),self.item);self.assertTrue((self.root/'original.hwp').is_file())
                with patch('workflow_ui.messagebox.showinfo') as info:controls['다음: 발신 정보'].invoke();info.assert_called_once()
                flow.outgoing_form.assert_not_called()
        finally:
            for token in root.tk.call('after','info'):root.after_cancel(token)
            root.destroy()

    def test_existing_private_library_gets_relief_without_replacing_its_forms(self):
        items=docs.catalog();self.assertEqual([x['id'] for x in items],['1','8'])
        self.assertEqual(items[0],self.item);self.assertEqual(docs.suggested_style({'category':'완화공문'})['id'],'8')
        self.assertEqual(items[1]['family'],'공문');self.assertEqual(items[1]['folder'],'3.완화신청')

    def test_rename_keeps_id_file_and_folder_and_rejects_duplicate_names(self):
        library.rename('1','수정한 양식 이름',self.root)
        self.assertEqual(docs.style('1'),{**self.item,'label':'수정한 양식 이름'})
        self.assertEqual((self.root/'original.hwp').read_bytes(),b'preserved original HWP')
        before=(self.root/'catalog.json').read_bytes()
        with self.assertRaises(ValueError):library.rename('1','완화공문',self.root)
        self.assertEqual(before,(self.root/'catalog.json').read_bytes())

    def test_remove_hides_form_without_breaking_existing_document_or_restoring_after_reload(self):
        library.remove('1',self.root);library.remove('8',self.root)
        self.assertEqual(docs.catalog(),[]);self.assertEqual(docs.style('1'),self.item)
        self.assertTrue((self.root/'original.hwp').exists());self.assertEqual(docs.catalog(),[])

    def test_bind_relief_original_preserves_source_anchors_and_stable_id(self):
        text='서울센터 {{발신번호}}\n({{발신일}})';source=self.base/'완화공문 원본.hwp';source.write_bytes(b'user form bytes')
        settings={**library.suggestions(text),'label':'완화공문','family':'공문','folder':str(self.base/'3.완화신청')}
        with patch.object(library,'inspect',return_value=text):item=library.add(source,settings,self.root,replace_id='8')
        self.assertEqual(item['id'],'8');self.assertEqual(item['role'],'relief_reply');self.assertEqual(item['family'],'공문')
        self.assertEqual((self.root/item['file']).read_bytes(),source.read_bytes());self.assertEqual(item['old_number'],'서울센터 {{발신번호}}')
        self.assertEqual([x['id'] for x in docs.catalog()],['1','8']);self.assertTrue((self.root/'original.hwp').exists())

    def test_new_document_does_not_suggest_a_deleted_template(self):
        library.remove('1',self.root)
        self.assertNotEqual(docs.suggested_style({'category':'기초센터 공문'})['id'],'1')
        library.remove('8',self.root)
        with self.assertRaisesRegex(ValueError,'추가'):docs.suggested_style({'category':'완화공문'})

    def test_management_actions_visible_in_small_window_and_changes_preserve_path(self):
        from modern_ui import apply_theme
        from outgoing_editor import folder_settings_dialog
        root=tk.Tk();apply_theme(root);root.geometry('1000x660')
        try:
            app=SimpleNamespace(root=root,data=self.base,config={'root':str(self.base)},commands=queue.Queue(),persist_config=lambda:None)
            win=folder_settings_dialog(app,root);win.geometry('850x480');root.update()
            self.assertIn('8',win.template_fields);win.template_fields['1'].set(str(self.base/'unsaved folder'))
            win.template_title_fields['1'].set('수정 중인 기본 제목')
            body=win.template_panel.body
            button=next(x for x in body.winfo_children() if x.winfo_class()=='TButton' and x.cget('text')=='이름 변경')
            with patch('outgoing_editor.simpledialog.askstring',return_value='새 표시 이름'):button.invoke()
            self.assertEqual(docs.style('1')['label'],'새 표시 이름');self.assertEqual(win.template_fields['1'].get(),str(self.base/'unsaved folder'))
            self.assertEqual(win.template_title_fields['1'].get(),'수정 중인 기본 제목')
            root.update()
            for child in win.winfo_children():
                if child.winfo_class()=='TFrame' and child is not win.template_panel:
                    for widget in child.winfo_children():
                        if widget.winfo_class()=='TButton':
                            self.assertGreaterEqual(widget.winfo_height(),widget.winfo_reqheight())
                            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),win.winfo_rooty()+win.winfo_height())
            delete=next(x for x in body.winfo_children() if x.winfo_class()=='TButton' and x.cget('text')=='삭제')
            with patch('outgoing_editor.messagebox.askyesno',return_value=True):delete.invoke()
            self.assertNotIn('1',win.template_fields);self.assertEqual(docs.style('1')['label'],'새 표시 이름');self.assertTrue((self.root/'original.hwp').exists())
            win.destroy()
        finally:
            for token in root.tk.call('after','info'):root.after_cancel(token)
            root.destroy()
