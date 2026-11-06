from pathlib import Path
import queue
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import relief_intake as relief
from folder_naming import describe_directory,folder_name,sequence_from_names
from route_chooser import RouteChooser


class ReliefIntakeTests(unittest.TestCase):
    def test_relief_category_uses_regular_receipt_and_relief_materials_folder(self):
        import core
        import folder_naming as naming
        app,job,dialog=self.fixture()
        self.assertIn('완화공문',naming.AGENCIES);self.assertIn('완화공문',core.CATEGORIES)
        self.assertNotIn(relief.LOCATION,naming.AGENCIES['서울시청 공문'])
        dialog.agency.set('완화공문');dialog.receipt_type.set('처리지침');dialog.load()
        self.assertEqual(dialog.receipt_type.get(),'공문')
        record=dialog.records.copy()
        # load clears the old tree; deliver the selected agency's own destination.
        folder=Path(app.config['root'])/relief.LOCATION
        dialog.deliver({'owner':dialog.owner,'request':dialog.request,'records':[{**describe_directory(folder),'relative':relief.LOCATION}]})
        self.assertEqual(dialog.selected,{relief.LOCATION})
        self.assertEqual(dialog.child_sets[relief.LOCATION],['relief'])
        self.assertEqual(naming.child_folders('완화공문',['relief']),{'relief':'1.완화신청자료'})
        dialog.relief_vars['district'].set('서대문구청');dialog.apply(False)
        _,data=list(app.commands.queue)[-1]
        self.assertEqual(data['fields']['receipt_type'],'공문');self.assertEqual(data['routing']['agency'],'완화공문')

    def test_title_variants_keep_address_and_additional_case_suffix(self):
        values=relief.source_fields({'title':'다른 제목 (예시동 1-1)외1','issuer':'서대문구청장'})
        self.assertEqual(values,{'district':'서대문구청','subject':'예시동 1-1','suffix':'외1'})
        self.assertEqual(relief.receipt_title(values['subject'],values['suffix']),relief.TITLE+' (예시동 1-1)외1')
        title=relief.folder_title(values['district'],values['subject'],values['suffix'])
        self.assertEqual(folder_name(relief.default_template('{number:03d}. {title}'),title,'2026-11-05',5),'005.26.11.05._[서대문구청] '+relief.TITLE+'(예시동 1-1)외1')

    def test_district_is_not_guessed_from_neighborhood_and_required_fields_are_checked(self):
        values=relief.source_fields({'title':'다른 제목 (예시동 1-1)'})
        self.assertEqual(values['district'],'')
        for district,subject in (('','예시동 1-1'),('강남구','예시동 1-1'),('강남구청','')):
            with self.assertRaises(ValueError):relief.validate({'district':district,'subject':subject,'receipt_title':'제목'})

    def fixture(self,job=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        base=Path(temporary.name).resolve()
        root=tk.Tk();root.withdraw()
        def close():
            root.update_idletasks()
            for token in root.tk.call('after','info'):root.after_cancel(token)
            root.destroy()
        self.addCleanup(close)
        app=SimpleNamespace(root=root,config={'root':str(base)},commands=queue.Queue(),busy=False)
        job=job or {'hash':'fixture','title':'기존 표현 (예시동 1-1)','issuer':'서울시청','category':'서울시청 공문','date':'2026-11-05','files':[]}
        dialog=RouteChooser(app,job);dialog.win.after_cancel(dialog.initial_after);dialog.request='fixture'
        records=[]
        for name in ('0.시청공문',relief.LOCATION):
            parent=base/name;parent.mkdir()
            if name==relief.LOCATION:(parent/('004.26.11.04._[서대문구청] '+relief.TITLE+'(이전 주소)')).mkdir()
            records.append({**describe_directory(parent),'relative':name})
        dialog.deliver({'owner':dialog.owner,'request':'fixture','records':records})
        return app,job,dialog

    def test_relief_selection_builds_separate_ledger_and_folder_titles_and_preserves_other_location(self):
        app,job,dialog=self.fixture();dialog.selected.update(('0.시청공문',relief.LOCATION));dialog.focus_folder(relief.LOCATION)
        dialog.relief_vars['district'].set('서대문구청')
        expected=relief.TITLE+' (예시동 1-1)'
        self.assertEqual(dialog.registration_title(),expected)
        target=next(t for t in dialog.targets() if t['relative']==relief.LOCATION)
        self.assertEqual(sequence_from_names(dialog.records[relief.LOCATION]['names'],target['template']),5)
        dialog.redraw_preview();self.assertIn('005.26.11.05._[서대문구청]',dialog.preview.get('1.0','end'))
        self.assertEqual(dialog.folder_titles['0.시청공문'],job['title'])
        dialog.folder_title.set('직접 수정한 폴더 제목');dialog.focus_folder('0.시청공문');dialog.focus_folder(relief.LOCATION)
        self.assertEqual(dialog.folder_title.get(),'직접 수정한 폴더 제목')
        dialog.relief_vars['receipt_title'].set(expected+' 수정')
        dialog.relief_vars['district'].set('종로구청')
        self.assertEqual(dialog.relief_vars['receipt_title'].get(),expected+' 수정')
        self.assertEqual(dialog.folder_title.get(),'직접 수정한 폴더 제목')
        dialog.apply(False)
        command,data=list(app.commands.queue)[-1];self.assertEqual(command,'route_apply')
        self.assertEqual(data['fields']['title'],expected+' 수정');self.assertNotIn('[서대문구청]',data['fields']['title'])
        self.assertTrue(data['routing']['relief']['defaults_applied'])
        self.assertEqual(job['title'],'기존 표현 (예시동 1-1)')

    def test_deselecting_relief_restores_original_receipt_title(self):
        _,job,dialog=self.fixture();dialog.selected.add(relief.LOCATION);dialog.focus_folder(relief.LOCATION)
        self.assertNotEqual(dialog.registration_title(),job['title'])
        dialog.selected.remove(relief.LOCATION);dialog.redraw_preview()
        self.assertEqual(dialog.registration_title(),job['title']);dialog.close()

    def test_incomplete_relief_inputs_stop_before_queueing_registration(self):
        app,_,dialog=self.fixture();dialog.selected.add(relief.LOCATION);dialog.focus_folder(relief.LOCATION)
        count=len(app.commands.queue)
        with patch('route_chooser.messagebox.showerror') as error:dialog.apply(True)
        error.assert_called_once();self.assertEqual(len(app.commands.queue),count);dialog.close()
