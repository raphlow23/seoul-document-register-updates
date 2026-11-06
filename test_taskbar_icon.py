from pathlib import Path
import tempfile
import unittest
import json
from app_icon import APP_ID


class TaskbarIconTests(unittest.TestCase):
    def test_native_icon_process_window_and_owned_shortcuts_agree(self):
        import os
        if os.name!='nt':self.skipTest('Windows taskbar identities')
        from taskbar_smoke import run
        with tempfile.TemporaryDirectory() as temporary:
            result=run(Path(temporary)/'result.json')
            self.assertEqual(result['app_id'],APP_ID);self.assertEqual(result['process_app_id'],APP_ID)
            self.assertTrue(result['big_icon']);self.assertTrue(result['small_icon'])
            self.assertTrue(result['other_shortcut_preserved']);self.assertTrue(result['persistent_icon_after_window_close'])
