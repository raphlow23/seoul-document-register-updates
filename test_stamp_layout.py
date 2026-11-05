from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
import core
from browser_sheets import BrowserSession
from stamp_ledger import StampSheets,STAMP_TAB
from test_browser_sheets import UI


class StampLayoutTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name).resolve()
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)

    def fixture(self,kind='공문',shift=0):
        folder=self.base/(kind+str(shift));folder.mkdir()
        rows=[['']*11 for _ in range(8)]
        rows[1]=['연번','날인일자','분류','','','문서번호','사용용도(문서제목)','수신처','신청부서','신청자','날인자']
        rows[2]=['','','공문','업무\n연락','처리\n지침','','','','','','']
        rows[4]=['432','2026-11-02','○','','','서울센터2026-440','이전 공문','수신 기관','편의증진부','담당자','']
        rows[5][0]='433';rows=[['']*11 for _ in range(shift)]+rows
        owner=StampSheets.__new__(StampSheets);owner.tab=STAMP_TAB;ui=UI(rows)
        owner.browser=BrowserSession({'root':str(folder),'spreadsheet_id':'fixture-sheet'},owner,ui=ui)
        job={'hash':'fixture-'+kind,'state':'발신 완료·직인 등록 대기','outgoing':{'id':'out-'+kind,'complete':True,'document_number':'서울센터2026-441','title':'새 발신 공문','receiver':'수신 기관','ledger_type':kind,'stamp':{'department':'편의증진부','applicant':'담당자','person':'','date':'2026-11-05'}}}
        return owner,ui,job

    def test_merged_two_row_header_registers_each_classification_in_correct_column(self):
        for kind,col in (('공문',2),('업무연락',3),('처리지침',4)):
            with self.subTest(kind=kind):
                owner,ui,job=self.fixture(kind);before=deepcopy(ui.rows[1:3])
                self.assertEqual(owner.register(job,self.store),(6,'433'))
                self.assertEqual(ui.rows[5][2:5],['○' if i==col else '' for i in range(2,5)])
                self.assertEqual(ui.rows[5][5:7],['서울센터2026-441','새 발신 공문'])
                self.assertEqual(ui.rows[5][8:11],['편의증진부','담당자','']);self.assertEqual(ui.rows[1:3],before)

    def test_shifted_header_rows_are_detected_without_changing_sheet_layout(self):
        owner,ui,job=self.fixture(shift=3);before=deepcopy(ui.rows[:6])
        self.assertEqual(owner.register(job,self.store),(9,'433'))
        self.assertEqual(owner.header_row,5);self.assertEqual(ui.rows[:6],before)

    def test_wrong_classification_columns_stop_before_any_write(self):
        owner,ui,job=self.fixture();ui.rows[2][3],ui.rows[2][4]=ui.rows[2][4],ui.rows[2][3]
        with self.assertRaisesRegex(ValueError,'머리글'):owner.register(job,self.store)
        self.assertEqual(ui.writes,[]);self.assertNotIn('sheet_attempted',job['outgoing']['stamp'])
