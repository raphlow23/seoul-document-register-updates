"""Actual Windows sharing locks, with a private Hancom stand-in and no business documents."""
import ctypes,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import hwp_documents as docs


@unittest.skipUnless(os.name=='nt','Windows file sharing semantics')
class HwpLockingTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name)
        self.item={'id':'fixture','file':'template.hwp','family':'공문','blank_body':True,'metadata_only':True,'title_fragments':[],'receiver':'','old_sender':'','old_number':'{{번호}}','old_date':'{{날짜}}','prefix':'','suffix':''}
        self.out={'number':'12','title':'잠금 검사 공문','receiver':'시험 기관','sender':'시험 담당','ledger_type':'공문','body':''}
        self.source=self.base/'template.hwp';self.source.write_bytes(b'original fixture')
        self.output=self.base/docs.filename(self.item,self.out);self.events=[];self.handles=[]
        from ctypes import wintypes as w
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];self.kernel.CreateFileW.restype=w.HANDLE
        self.kernel.CloseHandle.argtypes=[w.HANDLE];self.kernel.CloseHandle.restype=w.BOOL
        self.addCleanup(self.release)

    def lock(self,path,share):
        handle=self.kernel.CreateFileW(str(path),0x80000000,share,None,3,128,None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(handle)

    def release(self):
        while self.handles:self.kernel.CloseHandle(self.handles.pop())

    def run_export(self,share=0,clear_fails=False,save_fails=False,delayed=False):
        owner=self
        class Hancom:
            def Open(self,*args):owner.assertTrue(window.Visible);owner.events.append('open');return True
            def SaveAs(self,path,*args):
                owner.native=Path(path);owner.native.write_bytes(b'new fixture HWP');owner.lock(owner.native,share);owner.events.append('save')
                return not (save_fails is True or (save_fails=='result' and owner.native.name=='result.hwp'))
            def Clear(self,*args):
                owner.events.append('clear')
                if clear_fails:raise RuntimeError('fixture Clear failed')
            def Quit(self):
                owner.events.append('quit')
                if delayed:
                    timer=threading.Timer(.15,owner.release);timer.start();owner.addCleanup(timer.join)
                else:owner.release()
        window=SimpleNamespace(Visible=False);Hancom.XHwpWindows=SimpleNamespace(Item=lambda index:window)
        values=docs.native_values(self.item,self.out)
        def extract(path):
            self.events.append('extract');self.assertEqual(Path(path).read_bytes(),b'new fixture HWP')
            return values['number_date']+values['display_date']
        def verify(source,native):
            self.events.append('verify');self.assertEqual(Path(native).read_bytes(),b'new fixture HWP')
        with patch.object(docs,'template_root',return_value=self.base),patch.object(docs,'template_body',return_value=''),patch.object(docs,'replace_native_fields'),patch('core.extract',side_effect=extract),patch('hwp_export_check.check_baseline'),patch('hwp_fidelity.verify',side_effect=verify):
            return docs.export_hwp(self.item,self.out,self.output,factory=Hancom)

    def test_saved_document_is_closed_before_validation_copy_and_cleanup(self):
        for share in (0,1):
            with self.subTest(share=share):
                self.events=[];self.run_export(share=share)
                self.assertEqual(self.events,['open','save','save','clear','quit','extract','verify'])
                self.assertEqual(self.output.read_bytes(),b'new fixture HWP');self.output.unlink()
                self.assertFalse(self.native.parent.exists());self.assertEqual(self.source.read_bytes(),b'original fixture')

    def test_quit_releases_saved_file_even_if_clear_raises(self):
        self.run_export(clear_fails=True)
        self.assertEqual(self.output.read_bytes(),b'new fixture HWP');self.assertFalse(self.native.parent.exists())

    def test_delayed_delete_lock_release_is_waited_for_before_cleanup(self):
        self.run_export(share=1,delayed=True)
        self.assertFalse(self.native.parent.exists());self.assertFalse(self.handles)

    def test_save_failure_is_not_masked_by_temporary_file_cleanup(self):
        for failure in (True,'result'):
            with self.subTest(stage=failure),self.assertRaisesRegex(RuntimeError,'저장'):
                self.run_export(save_fails=failure)
            self.assertFalse(self.output.exists());self.assertFalse(self.native.parent.exists());self.assertIn('quit',self.events)


if __name__=='__main__':unittest.main()
