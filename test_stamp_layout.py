from pathlib import Path
import tempfile
import re
import unittest
from copy import deepcopy
import core
from browser_sheets import BrowserSession
from stamp_ledger import StampSheets,STAMP_TAB
from test_browser_sheets import UI


class FormattedUI(UI):
    def __init__(self,rows):
        super().__init__(rows);self.formats={(4,1):'hyphen',(5,1):'dots'};self.formats.update({(r,c):'prefix' for r in (4,5) for c in range(2,5) if (r,c)!=(4,2)})
        self.copies=[];self.ignore_formats=False

    def read(self,range_):
        from browser_ranges import bounds
        raw=super().read(range_)
        if re.fullmatch(r'A:[HK]',range_):start_row,start_col=1,1
        else:start_row,start_col,_,_=bounds(range_)
        for r,row in enumerate(raw,start_row-1):
            for c,value in enumerate(row,start_col-1):
                fmt=self.formats.get((r,c))
                if value and fmt=='prefix':row[c-start_col+1]='서울센터2025-'+value
                elif value and fmt=='dots':row[c-start_col+1]=value.replace('-','.')
        return raw

    def copy_format(self,source,target):
        from browser_ranges import bounds
        self.copies.append((source,target))
        if self.ignore_formats:return
        sr,sc,er,ec=bounds(source);tr,tc,br,bc=bounds(target)
        for r in range(tr-1,br):
            for c in range(tc-1,bc):self.formats[r,c]=self.formats.get((sr-1+(r-tr+1)%(er-sr+1),sc-1+(c-tc+1)%(ec-sc+1)))


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
        owner.browser=BrowserSession({'root':str(folder),'spreadsheet_id':'fixture-sheet','_program_dir':str(folder/'Program')},owner,ui=ui)
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

    def test_custom_classification_prefix_is_removed_and_date_matches_previous_row(self):
        owner,original,job=self.fixture();ui=FormattedUI(original.rows);owner.browser.ui=ui
        self.assertEqual(owner.register(job,self.store),(6,'433'))
        self.assertEqual(ui.read('B6:E6')[0],['2026-11-05','○','',''])
        self.assertEqual(ui.copies,[('A5:K5','A6:K6'),('C5','C6:E6')])
        self.assertEqual(ui.rows[5][8:11],['편의증진부','담당자',''])

    def test_old_failed_formatted_row_recovers_same_row_without_duplicate_or_rewriting_values(self):
        owner,original,job=self.fixture();ui=FormattedUI(original.rows);ui.ignore_formats=True;owner.browser.ui=ui
        with self.assertRaisesRegex(ValueError,'저장 결과'):owner.register(job,self.store)
        before=deepcopy(ui.rows);writes=list(ui.writes)
        self.assertEqual(ui.read('C6:C6')[0][0],'서울센터2025-○')
        ui.ignore_formats=False
        self.assertEqual(owner.register(job,self.store),(6,'433'))
        self.assertEqual(ui.rows,before);self.assertEqual(ui.writes,writes)
        self.assertEqual(ui.read('B6:C6')[0],['2026-11-05','○'])
        self.assertEqual(owner.register(job,self.store),(6,'433'))
        self.assertEqual(ui.writes,writes)
