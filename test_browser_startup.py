import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import browser_process
from browser_sheets import BrowserUI


class BrowserStartupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.ui=BrowserUI({"_auth_data_dir":temp.name,"spreadsheet_id":"test-sheet"})

    def test_manual_login_never_opens_remote_control(self):
        with patch.object(self.ui,"available",return_value=(None,[])),patch.object(self.ui,"executable",return_value=Path("C:/Browser/browser.exe")),patch("browser_sheets.launch_browser") as launch,patch("websocket.create_connection") as attach:
            self.ui.open_login()
        arguments=launch.call_args.args[0]
        self.assertFalse(any("debugging" in argument or "automation" in argument for argument in arguments))
        self.assertIn("--user-data-dir="+str(self.ui.profile),arguments)
        attach.assert_not_called()

    def test_manual_login_requires_closing_existing_controlled_window(self):
        with patch.object(self.ui,"available",return_value=(1234,[])),patch("browser_sheets.launch_browser") as launch:
            with self.assertRaisesRegex(ValueError,"먼저 모두 닫은"):
                self.ui.open_login()
        launch.assert_not_called()

    def test_google_login_page_is_not_automated_or_navigated(self):
        target={"type":"page","url":"https://accounts.google.com/ServiceLogin","webSocketDebuggerUrl":"ws://127.0.0.1:1234/devtools/page/test"}
        with patch.object(self.ui,"available",return_value=(1234,[target])),patch("websocket.create_connection") as attach:
            with self.assertRaisesRegex(ValueError,"일반 브라우저"):
                self.ui.connect()
        attach.assert_not_called()

    def test_connection_rejects_nonlocal_control_endpoint(self):
        target={"type":"page","url":"https://docs.google.com/spreadsheets/d/test-sheet/edit","webSocketDebuggerUrl":"ws://example.com:1234/control"}
        with patch.object(self.ui,"available",return_value=(1234,[target])),patch("websocket.create_connection") as attach:
            with self.assertRaisesRegex(ValueError,"연결 주소"):
                self.ui.connect()
        attach.assert_not_called()

    def test_frozen_dll_path_is_restored_when_browser_launch_fails(self):
        kernel=Mock();kernel.GetDllDirectoryW.side_effect=[0,0];kernel.SetDllDirectoryW.return_value=True
        with patch.object(browser_process.sys,"frozen",True,create=True),patch.object(browser_process.ctypes,"windll") as windll,patch("browser_process.subprocess.Popen",side_effect=OSError("launch failed")):
            windll.kernel32=kernel
            with self.assertRaisesRegex(OSError,"launch failed"):
                browser_process.launch(["C:/browser.exe"],self.ui.data/"test.log")
        self.assertEqual(kernel.SetDllDirectoryW.call_count,2)
        self.assertIsNone(kernel.SetDllDirectoryW.call_args_list[0].args[0])

    def test_invalid_port_does_not_contact_a_network_endpoint(self):
        (self.ui.profile/"DevToolsActivePort").write_text("99999\n")
        with patch("browser_sheets.build_opener") as opener:
            self.assertEqual(self.ui.available(),(None,[]))
        opener.assert_not_called()
