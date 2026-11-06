from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import shutil
import time
from native_dependencies import collect


class NativeDependencyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)

    def factory(self,mapping):
        class PE:
            def __init__(self,path):
                normal,delay=mapping[Path(path).name]
                self.DIRECTORY_ENTRY_IMPORT=[SimpleNamespace(dll=n.encode()) for n in normal]
                self.DIRECTORY_ENTRY_DELAY_IMPORT=[SimpleNamespace(dll=n.encode()) for n in delay]
            def __enter__(self):return self
            def __exit__(self,*_):pass
        return PE

    def test_missing_curl_is_rejected_instead_of_borrowing_developer_path(self):
        (self.root/'tesseract.exe').write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError,'libcurl.dll'):
            collect(self.root,pe_factory=self.factory({'tesseract.exe':(['KERNEL32.dll','libcurl.dll'],[])}))

    def test_normal_and_delayed_imports_are_collected_recursively(self):
        mapping={'tesseract.exe':(['libcurl.dll','KERNEL32.dll'],['delayed.dll']),
                 'libcurl.dll':(['crypto.dll'],[]),'crypto.dll':(['api-ms-win-crt-runtime-l1-1-0.dll'],[]),'delayed.dll':([],[])}
        for name in mapping:(self.root/name).write_bytes(b'fixture')
        self.assertEqual(set(collect(self.root,pe_factory=self.factory(mapping))),set(mapping))

    def test_missing_native_dll_exits_without_a_windows_error_popup(self):
        import os
        if os.name!='nt':self.skipTest('Windows native loader check')
        source=Path(__file__).parent/'ocr-runtime'/'tesseract.exe'
        if not source.is_file():self.skipTest('Native runtime not built')
        target=self.root/'tesseract.exe';shutil.copy2(source,target)
        from ocr import run_native
        start=time.monotonic();result=run_native([str(target),'--version'],self.root,10)
        self.assertNotEqual(result.returncode,0);self.assertLess(time.monotonic()-start,10)
