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
