from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from browser_sheets import BrowserUI
from browser_tabs import probe


class BrowserTabTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.ui=BrowserUI({"_auth_data_dir":temporary.name,"spreadsheet_id":"test-sheet"})
        self.ui.ready=Mock();self.ui.call=Mock()

    def test_already_selected_tab_does_not_click_or_open_its_menu(self):
        self.ui.evaluate=Mock(return_value={"matches":1,"active":True,"names":["접수"]})
        self.ui.tab("접수")
        self.ui.call.assert_not_called()

    def test_waits_for_tab_bar_to_load_after_editor_is_ready(self):
        self.ui.evaluate=Mock(side_effect=[{"matches":0,"active":False,"names":[]},{"matches":1,"active":True,"names":["접수"]}])
        with patch("browser_sheets.time.sleep") as sleep:self.ui.tab("접수")
        sleep.assert_called_once();self.ui.call.assert_not_called()

    def test_selects_control_without_pointer_drag_and_verifies_active_tab(self):
        self.ui.evaluate=Mock(side_effect=[{"matches":1,"active":False,"names":["접수","발행"]},{"matches":1,"visible":True,"x":120,"y":40},{"matches":1,"active":True}])
        with patch("browser_sheets.time.sleep"):self.ui.tab("발행")
        self.ui.call.assert_not_called()
        self.assertIn('.click()',self.ui.evaluate.call_args_list[1].args[0])

    def test_ambiguous_names_are_never_clicked(self):
        self.ui.evaluate=Mock(return_value={"matches":2,"names":["접수","접수"]})
        with self.assertRaisesRegex(ValueError,"여러 개"):self.ui.tab("접수")
        self.ui.call.assert_not_called()

    def test_missing_tab_reports_observed_names_without_guessing(self):
        self.ui.evaluate=Mock(return_value={"matches":0,"names":["다른 대장"]})
        with patch("browser_sheets.time.monotonic",side_effect=[0,0,26]),patch("browser_sheets.time.sleep"):
            with self.assertRaisesRegex(ValueError,"다른 대장"):self.ui.tab("접수")
        self.ui.call.assert_not_called()

    def test_names_are_json_escaped_and_normalized(self):
        script=probe('접수" 시험')
        self.assertIn('normalize(\'NFKC\')',script)
        self.assertIn('.click()',script)
        self.assertNotIn('scrollIntoView',script)
        self.assertNotIn('접수" 시험',script)

    def test_format_copy_uses_format_only_paste_and_checks_both_ranges(self):
        self.ui.select=Mock(side_effect=['C447','C448:E448']);self.ui.key=Mock()
        with patch('browser_sheets.time.sleep'):self.ui.copy_format('C447','C448:E448')
        self.assertEqual(self.ui.key.call_args_list[0].args,('c',))
        self.assertEqual(self.ui.key.call_args_list[1].kwargs,{'ctrl':True,'alt':True})
        self.ui.select=Mock(return_value='C449:E449');self.ui.key.reset_mock()
        with self.assertRaisesRegex(ValueError,'범위'):self.ui.copy_format('C447','C448:E448')
        self.ui.key.assert_not_called()
