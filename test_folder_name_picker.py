from pathlib import Path
import tempfile
import unittest
import tkinter as tk
from unittest.mock import patch
from types import SimpleNamespace
import queue
from folder_name_picker import ActualNamePicker,split_name
from folder_naming import describe_directory,preferred_template
from route_chooser import RouteChooser


class FolderNamePickerTests(unittest.TestCase):
    def test_each_location_retains_its_title_children_and_year_only_choice(self):
        app=SimpleNamespace(root=self.root,config={'root':str(self.base)},commands=queue.Queue(),busy=False)
        job={'hash':'fixture','title':'대장 제목','date':'2026-11-05','category':'중앙센터 공문','files':[]}
        central='0.중앙센터 공문';guideline=str(Path('5.보건복지부 및 중앙처리지침')/'1. 중앙지침(처리지침, 업무연락 등)')
        location=self.base/guideline;location.mkdir(parents=True);(location/'2026년').mkdir()
        dialog=RouteChooser(app,job);dialog.win.after_cancel(dialog.initial_after);dialog.request='fixture'
        records=[{**describe_directory(self.base/p),'relative':p} for p in (central,guideline)]
        dialog.deliver({'owner':dialog.owner,'request':'fixture','records':records});dialog.selected.update((central,guideline))
        dialog.focus_folder(central);dialog.folder_title.set('중앙공문용 제목')
        dialog.make_children.set(True);dialog.toggle_children()
        dialog.focus_folder(guideline);dialog.folder_title.set('지침용 제목')
        dialog.make_children.set(False);dialog.toggle_children()
        dialog.focus_folder(central);self.assertEqual(dialog.folder_title.get(),'중앙공문용 제목');self.assertEqual(len(dialog.child_sets[central]),3)
        dialog.focus_folder(guideline);self.assertEqual(dialog.folder_title.get(),'지침용 제목');self.assertFalse(dialog.make_children.get())
        dialog.year_only.set(True);dialog.mode_changed();self.assertEqual(dialog.year_folder.get(),'2026년')
        dialog.focus_folder(central);dialog.redraw_preview();self.assertTrue(dialog.make_children.get())
        targets={t['relative']:t for t in dialog.targets()}
        self.assertEqual(targets[guideline]['storage_mode'],'year');self.assertEqual(targets[guideline]['children'],[])
        self.assertEqual(targets[central]['folder_title'],'중앙공문용 제목');self.assertEqual(job['title'],'대장 제목')
        preview=dialog.preview.get('1.0','end');self.assertIn('2026년',preview);self.assertIn('중앙공문용 제목',preview)
        dialog.close()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.close_root)
        self.folder=self.base/'0.중앙센터 공문';self.folder.mkdir()
        for name in ('003. 26.11.01. 이전 문서','004. 26.11.02. 이전 문서'):(self.folder/name).mkdir()

    def close_root(self):
        self.root.update_idletasks()
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def test_default_uses_existing_styles_and_displays_actual_title_date_and_next_number(self):
        record=describe_directory(self.folder)
        config={'folder_profiles':{'incoming:0.중앙센터 공문':{'path':record['path'],'template':'{date}_{title}'}}}
        template=tk.StringVar(value=preferred_template(record,config,'incoming:0.중앙센터 공문'));title=tk.StringVar(value='새 공문')
        picker=ActualNamePicker(self.root,template,title,lambda:record,lambda:'2026-11-03');picker.refresh()
        self.assertEqual(picker.value.get(),'005. 26.11.03. 새 공문')
        self.assertTrue(all('{' not in name for name in picker.box.cget('values')))
        title.set('바꾼 제목');picker.refresh();self.assertIn('바꾼 제목',picker.value.get())

    def test_manual_edit_uses_real_name_without_template_tokens(self):
        record=describe_directory(self.folder);template=tk.StringVar(value='{number:03d}. {title}');title=tk.StringVar(value='새 제목')
        picker=ActualNamePicker(self.root,template,title,lambda:record,lambda:'2026-11-03');picker.refresh()
        with patch('folder_name_picker.simpledialog.askstring',return_value='[005] 수정 제목'):picker.edit()
        self.assertEqual(title.get(),'수정 제목');self.assertNotIn('{',picker.value.get())
        self.assertEqual(split_name('처리지침006. 26.11.03. 새 지침')[1],'새 지침')

    def test_receipt_destination_immediately_populates_the_concrete_name(self):
        app=SimpleNamespace(root=self.root,config={'root':str(self.base)},commands=queue.Queue(),busy=False)
        job={'hash':'fixture','title':'새 제목','date':'2026-11-03','category':'중앙센터 공문','files':[]}
        dialog=RouteChooser(app,job);dialog.win.after_cancel(dialog.initial_after)
        dialog.request='fixture';record={**describe_directory(self.folder),'relative':'0.중앙센터 공문'}
        dialog.deliver({'owner':dialog.owner,'request':'fixture','records':[record]})
        self.assertEqual(dialog.name_picker.value.get(),'005. 26.11.03. 새 제목')
        self.assertEqual(len(dialog.examples.get(0,'end')),2);dialog.close()

    def test_sequence_style_is_recognized_when_title_starts_with_korean_year(self):
        from folder_naming import infer_template
        self.assertEqual(infer_template('001. 2026년 시설 조사'),'{number:03d}. {title}')

    def test_architecture_and_use_approval_have_separate_selectable_children(self):
        from folder_naming import child_kinds,child_folders
        names=['1.건축허가&사업계획승인','2.사용승인&사용검사']
        app=SimpleNamespace(root=self.root,config={'root':str(self.base)},commands=queue.Queue(),busy=False)
        job={'hash':'fixture','title':'새 제목','date':'2026-11-05','category':'서울시청 공문','files':[]}
        dialog=RouteChooser(app,job);dialog.win.after_cancel(dialog.initial_after);dialog.request='fixture'
        records=[]
        for name in names:
            folder=self.base/name;folder.mkdir();records.append({**describe_directory(folder),'relative':name})
        dialog.deliver({'owner':dialog.owner,'request':'fixture','records':records})
        for name in names:dialog.selected.add(name)
        dialog.focus_folder(names[0]);dialog.redraw_preview()
        self.assertEqual(list(child_folders('서울시청 공문',dialog.child_sets[names[0]]).values()),['1.시청공문','2.협의도면','3.시청회신'])
        dialog.focus_folder(names[1]);dialog.redraw_preview()
        self.assertEqual(list(child_folders('서울시청 공문',dialog.child_sets[names[1]]).values()),['1.시청공문','2.협의도면','3.현장사진','4.시청회신'])
        dialog.children['photos'].set(False);dialog.children_changed()
        self.assertEqual(list(child_folders('서울시청 공문',dialog.child_sets[names[1]]).values()),['1.시청공문','2.협의도면','3.시청회신'])
        dialog.focus_folder(names[0]);self.assertTrue(dialog.children['drawings'].get());dialog.close()
