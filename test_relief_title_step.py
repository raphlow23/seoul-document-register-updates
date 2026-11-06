from pathlib import Path
import queue
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from title_chooser import TitleChooser
import relief_intake as relief


class ReliefTitleStepTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.base=Path(temporary.name)
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.close)
        fields={k:tk.StringVar(value=v) for k,v in {'category':'중앙센터 공문','receipt_type':'처리지침','date':'2026-11-06','recipient':'담당자','issuer':'기관','title':''}.items()}
        self.app=SimpleNamespace(root=self.root,fields=fields,commands=queue.Queue(),busy=False,workflow=SimpleNamespace(receipt_home=lambda:None))
        file=self.base/'예시동123-4 (완화승인신청서).pdf';file.write_bytes(b'fixture')
        self.job={'hash':'fixture','title':'통로 검토 문장','category':'중앙센터 공문','files':[{'path':str(file),'hash':'file'}]}
        parent=tk.Frame(self.root);parent.pack();self.editor=TitleChooser(self.app,[self.job],parent=parent)
        self.editor.win.after_cancel(self.editor.initial_after)

    def close(self):
        self.root.update_idletasks()
        if getattr(self.app,'title_dialog',self.editor) is not None and self.editor.win.winfo_exists():self.editor.close()
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def payload(self):
        self.editor.request='fixture'
        return {'owner':self.editor.owner,'request':'fixture','text':'관련 서식 본문','options':[{'label':'본문','title':'관련 없는 구조 검토 문장','mode':'manual'}],'page_count':1,'image':None}

    def test_selecting_relief_immediately_sets_regular_receipt_title_before_ocr(self):
        self.app.fields['category'].set('완화공문')
        expected='완화된 세부기준의 승인신청에 따른 의견 요청(예시동 123-4)'
        self.assertEqual(self.editor.title.get('1.0','end').strip(),expected)
        self.assertEqual(self.app.fields['receipt_type'].get(),'공문')
        self.editor.deliver(self.payload())
        self.assertEqual(self.editor.title.get('1.0','end').strip(),expected)

    def test_manual_edits_survive_delayed_ocr_result_and_category_trace_is_removed(self):
        self.app.fields['category'].set('완화공문');self.editor.title.insert('end',' 수정');self.root.update()
        value=self.editor.title.get('1.0','end').strip();self.assertTrue(self.editor.relief_manual_title)
        self.editor.deliver(self.payload());self.assertEqual(self.editor.title.get('1.0','end').strip(),value)
        self.editor.close();self.app.fields['category'].set('서울시청 공문')

    def test_form_caption_is_not_used_as_the_address(self):
        self.assertEqual(relief.source_fields(self.job)['subject'],'예시동 123-4')
