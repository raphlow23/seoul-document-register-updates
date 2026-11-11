import unittest
from unittest.mock import Mock,patch
from browser_ranges import align
from browser_sheets import BrowserUI


class BrowserRangeTests(unittest.TestCase):
    def test_grid_end_retries_when_shortcut_stays_in_header(self):
        ui=BrowserUI.__new__(BrowserUI);ui.select=Mock();ui.key=Mock()
        ui.selection=Mock(side_effect=[{'range':'A1'},{'range':'V1002'}])
        with patch('browser_sheets.time.sleep'):self.assertEqual(ui.grid_rows(),1002)
        self.assertEqual(ui.select.call_count,2);self.assertEqual(ui._grid_row_count,1002)

    def test_grid_end_never_guesses_row_count_when_focus_is_not_in_grid(self):
        ui=BrowserUI.__new__(BrowserUI);ui.select=Mock();ui.key=Mock();ui.selection=Mock(return_value={'range':'A2:H2'})
        with patch('browser_sheets.time.sleep'),self.assertRaisesRegex(ValueError,'마지막 행'):ui.grid_rows()
        self.assertFalse(hasattr(ui,'_grid_row_count'))
    def test_hidden_leading_row_is_padded_without_shifting_data(self):
        text="\t\t\n연번\t일자\t제목\n1\t2026-11-03\t기존 공문"
        rows=align(text,"A1:C4","A2:C4")
        self.assertEqual(rows[0],["","",""]);self.assertEqual(rows[2],["연번","일자","제목"]);self.assertEqual(rows[3][2],"기존 공문")

    def test_quoted_multiline_headers_keep_physical_rows(self):
        rows=align('연번\t"FAX/우편/\n인편/Email"\n1\tEmail',"A3:B4","A3:B4")
        self.assertEqual(len(rows),2);self.assertIn("\n",rows[0][1])

    def test_filtered_or_wrong_copied_range_is_rejected(self):
        for text,requested,selected in (("a\tb","A1:B3","A1:B3"),("A:H","A1:H4","A1:H4"),("1\t2","A6:B6","A7:B7")):
            with self.subTest(requested=requested),self.assertRaises(ValueError):align(text,requested,selected)

    def test_empty_cells_keep_row_count(self):
        self.assertEqual(len(align("\t\r\n\t\r\n\t","A1:B3","A1:B3")),3)

    def test_write_refuses_another_selected_cell(self):
        ui=BrowserUI.__new__(BrowserUI);ui.select=Mock(return_value="G6");ui.clipboard=Mock();ui.key=Mock()
        with self.assertRaises(ValueError):ui.write("F6","new title")
        ui.clipboard.assert_not_called();ui.key.assert_not_called()

    def test_read_waits_past_empty_clipboard_and_aligns_range(self):
        ui=BrowserUI.__new__(BrowserUI);ui.select=Mock(return_value="A2:B3");ui.key=Mock()
        content=iter(["","a\tb\n1\t2","a\tb\n1\t2"])
        ui.clipboard=lambda value=None: value if value is not None else next(content)
        rows=ui.read("A1:B3")
        self.assertEqual(rows,[["",""],["a","b"],["1","2"]])


if __name__=="__main__":unittest.main()
