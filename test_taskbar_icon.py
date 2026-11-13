from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import Mock,patch
from app_icon import APP_ID


class TaskbarIconTests(unittest.TestCase):
    def test_unrelated_network_shortcut_is_never_resolved_or_rewritten(self):
        from app_icon import repair_shortcuts
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);exe=root/'SeoulDocumentRegister.exe';exe.write_bytes(b'fixture')
            link=Mock();link.GetPath.return_value=(r'\\unreachable.invalid\share\SeoulDocumentRegister.exe',0)
            resolved=[];original=Path.resolve
            def resolve(path,*args,**kwargs):
                resolved.append(str(path));return original(path,*args,**kwargs)
            with patch('pythoncom.CoCreateInstance',return_value=link),patch.object(Path,'resolve',resolve):
                result=repair_shortcuts(exe,root/'icon.ico',[root/'unrelated.lnk'])
            self.assertEqual(result,{'updated':[],'errors':[]})
            self.assertEqual(resolved,[str(exe)]);link.SetIconLocation.assert_not_called()

    def test_shortcut_maintenance_reuses_verified_cache_and_invalidates_on_update(self):
        from app_icon import prepare_desktop_identity
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);exe=root/'program.exe';exe.write_bytes(b'old executable')
            icon=root/'icon.ico';icon.write_bytes(b'owned icon')
            with patch('app_icon.persistent_icon',return_value=icon),patch('app_icon.repair_shortcuts',return_value={'updated':[],'errors':[]}) as repair:
                prepare_desktop_identity(root,exe)
                self.assertTrue(prepare_desktop_identity(root,exe)['cached']);self.assertEqual(repair.call_count,1)
                exe.write_bytes(b'new executable with different size')
                prepare_desktop_identity(root,exe);self.assertEqual(repair.call_count,2)
                (root/'taskbar-shortcut-status.json').write_text('broken cache',encoding='utf-8')
                prepare_desktop_identity(root,exe);self.assertEqual(repair.call_count,3)

    def test_shortcut_checks_start_after_first_paint_on_their_own_com_thread(self):
        from app_icon import schedule_desktop_identity
        root=Mock();scheduled=[];root.after.side_effect=lambda delay,callback:scheduled.append((delay,callback))
        workers=[]
        class Thread:
            def __init__(self,**kwargs):workers.append(kwargs)
            def start(self):pass
        with patch('app_icon.threading.Thread',Thread),patch('app_icon.prepare_desktop_identity') as prepare:
            schedule_desktop_identity(root,'fixture-data','fixture.exe')
            prepare.assert_not_called();self.assertFalse(workers)
            scheduled[0][1]();self.assertEqual(len(workers),1);prepare.assert_not_called()
            with patch('pythoncom.CoInitialize') as initialize,patch('pythoncom.CoUninitialize') as uninitialize:
                workers[0]['target']()
            initialize.assert_called_once();uninitialize.assert_called_once()
            prepare.assert_called_once_with('fixture-data','fixture.exe')
            self.assertTrue(workers[0]['daemon'])

    def test_native_icon_process_window_and_owned_shortcuts_agree(self):
        import os
        if os.name!='nt':self.skipTest('Windows taskbar identities')
        from taskbar_smoke import run
        with tempfile.TemporaryDirectory() as temporary:
            result=run(Path(temporary)/'result.json')
            self.assertEqual(result['app_id'],APP_ID);self.assertEqual(result['process_app_id'],APP_ID)
            self.assertTrue(result['big_icon']);self.assertTrue(result['small_icon'])
            self.assertTrue(result['other_shortcut_preserved']);self.assertTrue(result['persistent_icon_after_window_close'])
