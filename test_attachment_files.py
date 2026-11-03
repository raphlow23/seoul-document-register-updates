from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
import tkinter as tk
from types import SimpleNamespace
import core
from app import Worker
from attachment_files import eligible,FILE_TYPES


class AttachmentFileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.downloads=self.base/'Downloads';self.downloads.mkdir()

    def files(self,names):
        result=[]
        for name in names:
            file=self.downloads/name;file.write_bytes(('attachment '+name).encode());result.append(file)
        return result

    def test_watch_inventory_includes_all_formats_but_not_incomplete_or_office_lock_files(self):
        accepted=self.files(['붙임.xls','붙임.xlsx','자료.xlsm','표.csv','양식.docx','사진.png','도면.dwg','이름만'])
        ignored=self.files(['양식.tmp','data.crdownload','other.part','~$붙임.xlsx','desktop.ini'])
        worker=Worker(queue.Queue(),queue.Queue(),{'downloads':str(self.downloads)},self.base/'unused.sqlite')
        found=worker.files();self.assertEqual(set(found),{str(p) for p in accepted})
        self.assertTrue(all(not eligible(p) for p in ignored))

    def test_nontext_attachments_are_not_parsed_or_executed_and_copy_unchanged(self):
        files=self.files(['입력.xlsx','사진.jpg','파일.unknown','파일.exe'])
        target=self.base/'shared';target.mkdir();(target/'case').mkdir()
        with patch('core.extract',side_effect=AssertionError('Do not parse attachments')) as extract:
            for file in files:
                job,new=core.ingest(file,self.store)
                self.assertTrue(new);self.assertEqual(job['text'],'');self.assertIn('첨부 보관용',job['reason'])
                saved=core.copy_files(job,target,subfolders=['case'])
                self.assertEqual(Path(saved[0]).read_bytes(),file.read_bytes())
        extract.assert_not_called()

    def test_file_dialog_defaults_to_all_files(self):
        self.assertEqual(FILE_TYPES[0][1],'*.*')

    def test_inaccessible_file_does_not_stop_download_inventory(self):
        with patch.object(Path,'is_file',side_effect=PermissionError('inaccessible')):
            self.assertFalse(eligible(self.downloads/'blocked.xlsx'))

    def test_title_picker_keeps_spreadsheets_but_starts_with_a_readable_document(self):
        from title_chooser import TitleChooser
        excel,pdf=self.files(['first.xlsx','main.pdf'])
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=SimpleNamespace(root=root,commands=queue.Queue(),busy=False)
        jobs=[{'hash':core.digest(p),'files':[{'hash':core.digest(p),'path':str(p)}],'title':p.stem} for p in (excel,pdf)]
        chooser=TitleChooser(app,jobs)
        self.assertEqual(Path(chooser.files[0]['path']),pdf)
        self.assertEqual(len(chooser.files),2);chooser.close()


if __name__=='__main__':unittest.main()
