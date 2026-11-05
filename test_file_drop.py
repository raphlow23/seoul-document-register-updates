from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock
from file_drop import create_root,FileDrop,COPY,REFUSE_DROP


class FileDropTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=create_root();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.zone=tk.Label(self.root,text="drop")
        self.app=SimpleNamespace(root=self.root,busy=False,updating=False,startup_gate=False,worker=SimpleNamespace(working=threading.Event()),status=tk.StringVar(master=self.root),send=Mock(return_value=True))
        self.drop=FileDrop(self.app,[self.root,self.zone])

    def event(self,*paths):
        return SimpleNamespace(data=self.root.tk.call("format","%s",self.root.tk.call("list",*[str(p) for p in paths])))

    def test_native_library_and_targets_load(self):
        self.assertTrue(self.root.tk.call("package","present","tkdnd"));self.assertEqual(len(self.drop.targets),2)

    def test_multiple_unicode_space_brace_paths_are_added_as_copy(self):
        files=[self.base/"공문 {서울}.PDF",self.base/"붙임 압축.zip"]
        for path in files:path.write_bytes(b"untouched")
        self.assertEqual(self.drop.receive(self.event(*files,files[0])),COPY)
        self.app.send.assert_called_once_with("add",[str(p) for p in files])
        self.assertTrue(all(p.read_bytes()==b"untouched" for p in files))

    def test_temporary_files_and_folders_are_not_imported(self):
        path=self.base/"file.crdownload";path.write_bytes(b"incomplete")
        self.assertEqual(self.drop.receive(self.event(path,self.base)),REFUSE_DROP);self.app.send.assert_not_called()

    def test_excel_word_images_and_other_completed_files_are_accepted(self):
        files=[self.base/name for name in ('붙임.XLSX','양식.xls','안내.docx','안내.html','사진.jpg','자료.dat','확장자없음')]
        for path in files:path.write_bytes(b'copy only')
        self.assertEqual(self.drop.receive(self.event(*files)),COPY)
        self.app.send.assert_called_once_with('add',[str(p) for p in files[:4]])

    def test_busy_rejects_without_losing_files(self):
        path=self.base/"document.hwp";path.write_bytes(b"fixture");self.app.busy=True
        self.assertEqual(self.drop.receive(self.event(path)),REFUSE_DROP);self.app.send.assert_not_called();self.assertTrue(path.exists())


if __name__=="__main__":unittest.main()
