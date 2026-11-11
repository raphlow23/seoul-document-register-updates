from copy import deepcopy
from pathlib import Path
import queue,tempfile,unittest
from unittest.mock import patch
from datetime import date
import core,outgoing
from outgoing import OutgoingSheets,require_registered_number
from browser_sheets import BrowserSession
from test_browser_sheets import UI


class OutgoingLayoutTests(unittest.TestCase):
    def test_adjacent_merged_title_labels_keep_physical_header_and_data_rows(self):
        for family in ('업무연락','처리지침'):
            owner,ui,job=self.fixture(family);ui.rows[3][4]='제 목'
            self.assertEqual(owner.register(job,self.store),(6,'438'))
            self.assertEqual(owner.header_row,2);self.assertEqual(ui.rows[3][4],'제 목')
            self.assertEqual(ui.rows[5][4],job['title'])

    def test_delayed_tab_content_is_rechecked_before_any_outgoing_write(self):
        owner,ui,job=self.fixture('업무연락');stale=[['']*8 for _ in ui.rows]
        stale[2]=['연번','일자','분류','공문','처리지침','분류번호','','제목']
        original=ui.read;reads=[]
        def delayed(range_):
            reads.append(range_)
            if len(reads)==1:return stale
            return original(range_)
        ui.read=delayed
        with patch('browser_sheets.time.sleep'):self.assertEqual(owner.register(job,self.store),(6,'438'))
        self.assertTrue(any(r.startswith('A1:H') for r in reads));self.assertEqual(ui.rows[5][4],job['title'])

    def test_wrong_header_records_labels_but_never_business_rows_or_writes(self):
        import json
        owner,ui,job=self.fixture('업무연락');ui.rows[2][4],ui.rows[2][5]=ui.rows[2][5],ui.rows[2][4]
        ui.rows[4][4]='민감한 실제 공문 제목'
        with patch('browser_sheets.time.sleep'),self.assertRaisesRegex(ValueError,'읽어 온 머리글'):owner.register(job,self.store)
        self.assertFalse(ui.writes)
        reports=list((self.base/'Program'/'임시보관'/'시트머리글').glob('*.json'));self.assertTrue(reports)
        report=reports[0].read_text(encoding='utf-8');self.assertNotIn('민감한 실제 공문 제목',report)
        self.assertEqual(json.loads(report)['tab'],owner.tab)

    def test_nonadjacent_header_blocks_are_still_rejected_without_writing(self):
        owner,ui,job=self.fixture('업무연락');ui.rows[6]=ui.rows[2].copy()
        with patch('browser_sheets.time.sleep'),self.assertRaisesRegex(ValueError,'머리글'):owner.register(job,self.store)
        self.assertFalse(ui.writes)

    def test_decomposed_korean_and_invisible_header_spacing_preserve_columns(self):
        import unicodedata
        for family in ('공문','업무연락','처리지침'):
            owner,ui,job=self.fixture(family)
            ui.rows[:4]=[[unicodedata.normalize('NFD',value).replace(' ','\u2060 ') for value in row] for row in ui.rows[:4]]
            before=deepcopy(ui.rows[:4]);self.assertEqual(owner.register(job,self.store),(6,'438'));self.assertEqual(ui.rows[:4],before)

    def test_hwp_filename_uses_actual_number_and_today_without_extra_gap(self):
        from hwp_documents import filename
        for prefix,suffix in (('',''),('처리지침','(센터-발송)')):
            item={'prefix':prefix,'suffix':suffix,'spacing':' '}
            self.assertEqual(filename(item,{'number':'9','title':'새 발신 제목'},date(2026,11,10)),f'{prefix}009.26.11.10. 새 발신 제목{suffix}.hwp')

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)

    def fixture(self,family='공문',shift=0):
        compact=family!='공문';width=8 if compact else 11
        rows=[['']*width for _ in range(8)]
        rows[1][0]='문 서 발 행 대 장'
        rows[2]=['연번','일 자','분 류 번 호','','제                  목','수 신 처','발 송 자','방 법'] if compact else ['연번','일 자','분 류','','','분 류 번 호','','제                  목','수 신 처','발 송 자','방 법']
        if not compact:rows[3][2:5]=['발송\n문서','내부\n결재','처리\n지침']
        rows[3][-1]='FAX/우편/\n인편/Email'
        rows[4]=['1','2026-11-02','서울센터2026-','437','이전 제목','기관','담당자','Email'] if compact else ['1','2026-11-02','○','','','서울센터2026-','437','이전 제목','기관','담당자','Email']
        rows[5][0]='2';rows[5][2 if compact else 5]='서울센터2026-';rows[5][3 if compact else 6]='438'
        rows=[['']*width for _ in range(shift)]+rows
        owner=OutgoingSheets.__new__(OutgoingSheets);owner.compact=compact;owner.last_column='H' if compact else 'K'
        owner.title_column=4 if compact else 7;owner.note_column='E' if compact else 'H';owner.prefix_col=2 if compact else 5;owner.number_col=3 if compact else 6
        owner.tab=outgoing.OUTGOING_TABS[family];ui=UI(rows)
        owner.browser=BrowserSession({'root':str(self.base),'spreadsheet_id':'fixture','_program_dir':str(self.base/'Program')},owner,ui=ui)
        job={'id':'fixture-'+family+str(shift),'hash':'fixture-'+family+str(shift),'title':'새 발신 제목','state':'완료','receipt_done':True,'files':[]}
        job['outgoing']=outgoing.defaults(job,{'recipient':'담당자'});job['outgoing'].update(enabled=True,receiver='수신 기관',ledger_type=family)
        return owner,ui,job

    def test_merged_multiline_header_and_shifted_rows_keep_correct_outgoing_columns(self):
        for shift in (0,6):
            owner,ui,job=self.fixture(shift=shift);before=deepcopy(ui.rows[:4+shift])
            self.assertEqual(owner.register(job,self.store),(6+shift,'438'))
            self.assertEqual(owner.header_row,2+shift);self.assertEqual(ui.rows[5+shift][7],job['title'])
            self.assertEqual(ui.rows[:4+shift],before);self.assertEqual(require_registered_number(job['outgoing']),'438')
            count=len(ui.writes);self.assertEqual(owner.register(job,self.store),(6+shift,'438'));self.assertEqual(len(ui.writes),count)

    def test_business_contact_and_guideline_compact_headers_use_their_own_numbers(self):
        for family in ('업무연락','처리지침'):
            owner,ui,job=self.fixture(family,3)
            self.assertEqual(owner.register(job,self.store),(9,'438'));self.assertEqual(ui.rows[8][4],job['title'])
            self.assertEqual(job['outgoing']['file_number'],'438')

    def test_wrong_columns_are_rejected_before_any_sheet_write(self):
        owner,ui,job=self.fixture();ui.rows[2][7],ui.rows[2][8]=ui.rows[2][8],ui.rows[2][7]
        with self.assertRaisesRegex(ValueError,'머리글'):owner.register(job,self.store)
        self.assertFalse(ui.writes);self.assertFalse(job['outgoing'].get('sheet_attempted'))

    def export_worker(self,out):
        from app import Worker
        folder=self.base/'save';folder.mkdir(exist_ok=True)
        job={'hash':'export','id':'export','title':'새 발신 제목','state':'완료','files':[],
             'outgoing':{'id':'out-export','enabled':True,'title':'새 발신 제목','template_id':'1','ledger_type':'공문','save_folder':str(folder),'filename_suffix':'','stamp':{},**out}}
        self.store.put(job);commands,events=queue.Queue(),queue.Queue();worker=Worker(commands,events,{'root':str(self.base)},self.base/'history.sqlite');exports=[]
        def save(item,out,path):exports.append(Path(path));Path(path).write_bytes(b'HWP fixture')
        with patch('hwp_documents.export_hwp',side_effect=save),patch('outgoing_folders.prepare') as prepare:
            worker.start();commands.put(('draft_export',{'hash':job['hash'],'outgoing':deepcopy(job['outgoing']),'path':str(folder/'001.wrong.hwp'),'native_hwp':True,'copy_only':False}));commands.put(('quit',None));worker.join(timeout=10)
            prepare.assert_not_called()
        return job,exports,[data for kind,data in events.queue if kind=='error']

    def test_manual_number_without_sheet_confirmation_never_saves_hwp(self):
        job,exports,errors=self.export_worker({'file_number':'001'})
        self.assertFalse(exports);self.assertTrue(any('발신대장' in error for error in errors))
        self.assertFalse(list((self.base/'save').iterdir()))

    def test_confirmed_number_overrides_stale_filename_and_manual_number(self):
        job,exports,errors=self.export_worker({'complete':True,'number':'438','document_number':'서울센터2026-438','file_number':'001'})
        self.assertFalse(errors,errors);self.assertEqual(len(exports),1)
        self.assertEqual(exports[0].name,f'438.{date.today():%y.%m.%d}. 새 발신 제목.hwp')
        self.assertEqual(self.store.get(job['hash'])['outgoing']['hwp_saved_number'],'438')
