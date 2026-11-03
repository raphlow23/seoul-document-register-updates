from pathlib import Path
import queue
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tkinter as tk
import re
import hashlib
import json
import zipfile

from ocr_text_editor import OcrTextEditor
from ocr_excerpt import clean,excerpt

SOURCE='가상 기관 원문\n제목 원본 제목\n1. 첫 항의 인사말입니다.\n2. 2026 년 편 의 시 설 조 사 를 실 시 합니다.\n3. 결 과 를 회 신 하여 주 시 기 바랍니다.\n붙 임 1. 조 사 표 1 부 . 끝 .\n기관장\n시행 가상기관 1234\n전화 00-0000'


class OcrReviewTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.close_root)
        self.app=SimpleNamespace(root=self.root,commands=queue.Queue(),busy=False)

    def close_root(self):
        self.root.update_idletasks()
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def automatic(self,text=SOURCE):
        editor=OcrTextEditor(self.app,text)
        self.addCleanup(lambda:editor.close() if editor.win.winfo_exists() else None)
        deadline=time.monotonic()+3
        while self.app.commands.empty():
            self.root.update();time.sleep(.01)
            if time.monotonic()>deadline:self.fail('Automatic cleaning did not start')
        command,data=self.app.commands.get_nowait();self.assertEqual(command,'ocr_clean')
        self.assertEqual(data['start'],2);self.assertTrue(data['attachments']);self.assertTrue(data['spacing'])
        return editor,data

    def test_opening_automatically_prepares_only_2_through_attachments_and_keeps_source(self):
        editor,data=self.automatic()
        import kiwipiepy_model
        model=Path(kiwipiepy_model.__file__).parent
        with patch('korean_spacing.model_path',return_value=model):result=clean(data['text'],{},data['start'],data['attachments'],data['spacing'])
        editor.deliver({**data,**result})
        value=editor.result.get('1.0','end').strip()
        self.assertTrue(editor.ready);self.assertTrue(value.startswith('2.'));self.assertTrue(value.endswith('끝.'))
        self.assertNotIn('첫 항',value);self.assertNotIn('제목 원본',value);self.assertNotIn('기관장',value)
        self.assertEqual(editor.source.get('1.0','end').strip(),SOURCE)
        self.assertEqual(re.sub(r'\s','',excerpt(SOURCE)),re.sub(r'\s','',value))
        editor.result.insert('end','\n직접 수정한 내용')
        with patch.object(editor.win,'clipboard_clear'),patch.object(editor.win,'clipboard_append') as copied:
            editor.copy()
        self.assertTrue(copied.call_args.args[0].endswith('직접 수정한 내용'))

    def test_missing_2_never_falls_back_to_greeting_or_whole_document(self):
        editor,data=self.automatic('제목 원문\n1. 인사말\n붙임 1. 자료. 끝.')
        with self.assertRaises(ValueError):excerpt(data['text'])
        editor.deliver({**data,'error':'본문 2항을 찾지 못했습니다.'})
        self.assertFalse(editor.ready);self.assertIsNone(editor.copy())
        self.assertNotIn('인사말',editor.result.get('1.0','end'))

    def test_body_2_can_start_with_year_and_fullwidth_numbering(self):
        self.assertTrue(excerpt('제목\n1. 인사\n2） 2026년 조사\n붙임 1. 자료. 끝.\n시행 번호').startswith('2）'))

    def test_pdf_original_preview_never_changes_source_bytes(self):
        from pypdf import PdfWriter
        from source_document_view import load
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'original.pdf';writer=PdfWriter();writer.add_blank_page(width=72,height=72)
            with path.open('wb') as file:writer.write(file)
            original=path.read_bytes();image,count=load(path)
            self.assertEqual(count,1);self.assertGreater(image.width,0);image.close()
            self.assertEqual(path.read_bytes(),original)

    def test_excel_attachment_does_not_trigger_a_repeated_empty_body_load(self):
        from workflow_ui import SimpleOutgoing
        from unittest.mock import Mock
        editor=SimpleOutgoing.__new__(SimpleOutgoing)
        editor.app=SimpleNamespace(workflow=SimpleNamespace(available=lambda:True))
        editor.job={'files':[{'path':'attachment.xlsx'}]};editor.file_box=SimpleNamespace(current=lambda:0);editor.load_source=Mock()
        with patch('workflow_ui.messagebox.showinfo') as notice:editor.prepare_excerpt()
        notice.assert_called_once();editor.load_source.assert_not_called()


class SpacingInstallTests(unittest.TestCase):
    def test_model_is_prepared_without_renaming_its_directory(self):
        import korean_spacing as spacing
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();archive=root/'model.zip'
            files={name:('fixture '+name).encode() for name in spacing.MODEL_FILES}
            info={'version':spacing.MODEL_VERSION,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
            with zipfile.ZipFile(archive,'w') as zipped:
                for name,data in files.items():zipped.writestr(name,data)
                zipped.writestr('BUILDINFO.json',json.dumps(info))
            archive=archive.rename(root/spacing.COMPONENT_NAME)
            with patch('updater.stage_component',return_value=archive),patch.object(Path,'rename',side_effect=PermissionError('directory in use')):
                path=spacing.model_path({'_auth_data_dir':str(root/'data'),'model_source':'fixture'})
            self.assertTrue(spacing.valid_model(path))
            self.assertEqual(spacing.model_path({'_auth_data_dir':str(root/'data')}),path)
