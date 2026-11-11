"""Owned grids prove reordered writes/retries/cancellation without touching business Sheets."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import re,tempfile,unittest
import core,outgoing,stamp_ledger
from browser_sheets import BrowserSession
from browser_journal import Journal
from ledger_layout import normalize,schema,column,index,MappedUI,APIMapper,default_values,apply_outgoing


class Grid:
    def __init__(self,rows):self.rows=rows;self.writes=[];self.copies=[];self.fail=None
    def tab(self,name):pass
    def close(self):pass
    def read(self,span):
        m=re.fullmatch(r'([A-Z]+)(\d*):([A-Z]+)(\d*)',span)
        if not m:raise AssertionError(span)
        a,b=index(m[1]),index(m[3]);first=int(m[2] or 1);last=int(m[4] or len(self.rows))
        return [r[a:b+1] for r in self.rows[first-1:last]]
    def write(self,cell,value):
        self.writes.append((cell,value))
        if self.fail==len(self.writes):raise OSError('fixture interrupted')
        m=re.fullmatch(r'([A-Z]+)(\d+)',cell);c=index(m[1]);r=int(m[2])-1
        if value.startswith('='):
            f=re.fullmatch(r'=([A-Z]+)(\d+)\+1',value);value=str(int(self.rows[int(f[2])-1][index(f[1])])+1)
        elif value.startswith("'"):value=value[1:]
        self.rows[r][c]=value
    def copy_format(self,source,target):self.copies.append((source,target))


class LayoutTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.config={'root':str(self.base),'spreadsheet_id':'owned-fixture','_program_dir':str(self.base/'Program'),'auth_mode':'browser'}
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)

    def fixture(self,scope,family='공문',shift=0):
        keys=schema(scope,family);width=len(keys)
        physical=[27,15,24,18,22,20,13,25,16,19,14][:width]
        layout=normalize({'columns':{k:column(c) for k,c in zip(keys,physical)},'header_range':f'N{3+shift}:AB{4+shift}', 'data_start':5+shift,'date_format':'YYYY. MM. DD.'},scope,family)
        rows=[['']*width for _ in range(8)]
        if scope=='receipt':
            owner=core.Sheets.__new__(core.Sheets);owner.tab='receipt'
            rows[2]=['연번','일자','문서번호','','발신처','제목','담당자','방법']
            rows[4]=['1','2026-11-01','서울센터2026-','20','기관','기존 제목','담당자','Email']
            rows[5][0]='2';rows[5][2:4]=['서울센터2026-','21']
        elif scope=='outgoing':
            compact=family!='공문';owner=outgoing.OutgoingSheets.__new__(outgoing.OutgoingSheets);owner.compact=compact;owner.last_column='H' if compact else 'K';owner.title_column=4 if compact else 7;owner.note_column=column(owner.title_column);owner.prefix_col=2 if compact else 5;owner.number_col=3 if compact else 6;owner.tab='outgoing'
            rows[2]=['연번','일자','분류번호','','제목','수신처','발송자','방법'] if compact else ['연번','일자','분류','','','분류번호','','제목','수신처','발송자','방법']
            if not compact:rows[3][2:5]=['발송문서','내부결재','처리지침']
            rows[4]=['1','2026-11-01','서울센터2026-','20','기존 제목','기관','담당자','Email'] if compact else ['1','2026-11-01','○','','','서울센터2026-','20','기존 제목','기관','담당자','Email']
            rows[5][0]='2';rows[5][owner.prefix_col]='서울센터2026-';rows[5][owner.number_col]='21'
        else:
            owner=stamp_ledger.StampSheets.__new__(stamp_ledger.StampSheets);owner.tab='stamp'
            rows[2]=['연번','날인일자','분류','','','문서번호','사용용도(문서제목)','수신처','신청부서','신청자','날인자']
            rows[3][2:5]=['공문','업무연락','처리지침']
            rows[4]=['1','2026-11-01','○','','','서울센터2026-20','기존 제목','기관','부서','담당','']
            rows[5][0]='2'
        rows=[['']*width for _ in range(shift)]+rows
        original=[['']*28 for _ in rows]
        for r,row in enumerate(rows):
            original[r][0]='연결하지 않은 열 유지'
            for c,v in enumerate(row):original[r][physical[c]]=v
        grid=Grid(original)
        cfg={**self.config,'layout':layout,'_outgoing_family':family}
        owner.browser=BrowserSession(cfg,owner,ui=grid)
        job={'hash':scope+family+str(shift),'files':[{'hash':'body','path':'unused.pdf'}], 'date':'2026-11-11','title':'새 제목','issuer':'발신 기관','recipient':'새 담당자','method':'FAX','state':'확인 필요','receipt_type':family}
        job['outgoing']=outgoing.defaults(job,{'recipient':'새 담당자'});job['outgoing'].update(enabled=True,receiver='새 수신처',ledger_type=family)
        if scope=='stamp':job['outgoing'].update(complete=True,document_number='서울센터2026-21')
        record=job if scope=='receipt' else job['outgoing'] if scope=='outgoing' else job['outgoing']['stamp']
        record['sheet_connection']={'spreadsheet_id':'owned-fixture','tab':owner.tab,'layout':layout}
        return owner,grid,job,physical

    def test_all_ledgers_reordered_beyond_k_keep_formulas_dates_and_unmapped_cells(self):
        for scope,family,shift in [('receipt','공문',0),('outgoing','공문',52),('outgoing','업무연락',0),('outgoing','처리지침',4),('stamp','업무연락',0)]:
            with self.subTest(scope=scope,family=family):
                owner,grid,job,physical=self.fixture(scope,family,shift)
                before=deepcopy(grid.rows);row,number=owner.register(job,self.store)
                self.assertEqual((row,number),(6+shift,'2' if scope=='stamp' else '21'))
                self.assertEqual(grid.rows[row-1][physical[1]],'2026. 11. 11.')
                self.assertEqual(grid.rows[row-1][physical[owner.title_column]],'새 제목')
                self.assertEqual(grid.rows[:row-1],before[:row-1])
                for r in grid.rows:self.assertEqual(r[0],'연결하지 않은 열 유지')
                writes=len(grid.writes);self.assertEqual(owner.register(job,self.store),(row,number));self.assertEqual(len(grid.writes),writes)
                self.assertTrue(all(index(re.match('[A-Z]+',cell)[0]) in physical for cell,_ in grid.writes))
                self.assertTrue(all('A' not in re.findall('[A-Z]+',source+target) for source,target in grid.copies))
                self.assertEqual(Journal(self.config).entries()[-1]['layout'],owner.layout)

    def test_reordered_partial_write_recovers_original_number(self):
        owner,grid,job,physical=self.fixture('outgoing');grid.fail=3
        with self.assertRaises(OSError):owner.register(job,self.store)
        grid.fail=None
        self.assertEqual(owner.register(job,self.store),(6,'21'))
        self.assertEqual(grid.rows[6][physical[7]],'')

    def test_cancel_uses_journal_layout_after_default_mapping_changes(self):
        import registration_cancel as cancel
        owner,grid,job,physical=self.fixture('receipt')
        owner.register(job,self.store);job.update(receipt_done=True,number='21',row=6)
        job.pop('outgoing');self.store.put(job)
        changed={**self.config,'ledger_connections':{'receipt:공문':{'layout':normalize({},'receipt','공문')}}}
        def factory(config,target):
            ledger=core.Sheets.__new__(core.Sheets);ledger.tab=target['tab']
            ledger.browser=BrowserSession({**config,'layout':target.get('layout')},ledger,ui=grid)
            return ledger
        cancel.execute(job,changed,self.store,'receipt',factory)
        for c in cancel.columns('receipt','공문'):self.assertEqual(grid.rows[5][physical[c]],'')
        self.assertEqual(grid.rows[5][physical[3]],'21');self.assertEqual(grid.rows[5][0],'연결하지 않은 열 유지')

    def test_bad_or_ambiguous_columns_and_header_ranges_reject_without_writes(self):
        for raw in ({'columns':{'title':'A'}},{'columns':{'title':'BA'}},{'header_range':'A3:K4'}, {'header_range':'A3:K4','data_start':3}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):normalize(raw,'outgoing','공문')
        owner,grid,job,physical=self.fixture('receipt');grid.rows[2][physical[5]]='다른 칸'
        with patch('browser_sheets.time.sleep'),self.assertRaisesRegex(ValueError,'머리글'):owner.register(job,self.store)
        self.assertFalse(grid.writes);self.assertFalse(job.get('sheet_attempted'))

    def test_explicit_header_range_does_not_guess_a_second_table(self):
        owner,grid,job,physical=self.fixture('receipt')
        grid.rows[2][physical[5]]='다른표';grid.rows[6]=grid.rows[2].copy();grid.rows[6][physical[5]]='제목'
        with patch('browser_sheets.time.sleep'),self.assertRaises(ValueError):owner.register(job,self.store)
        self.assertFalse(grid.writes)

    def test_api_mapper_maps_notes_formulas_and_formats_without_copying_unmapped_columns(self):
        owner,grid,job,physical=self.fixture('receipt')
        def raw(method,suffix='',**kwargs):
            if suffix.startswith('/values/'):
                from urllib.parse import unquote
                return {'values':grid.read(unquote(suffix).rsplit('!',1)[-1])}
            return {}
        transport=Mock(side_effect=raw);api=APIMapper(owner,self.config,transport)
        from urllib.parse import quote
        result=api('GET','/values/'+quote("'receipt'!D5:F5",safe=''))['values'][0]
        self.assertEqual(result,['20','기관','기존 제목'])
        reqs=core.registration_requests(1,6,job,owner.browser.getrows('A5:H5')[0],'SDR:fixture')
        api('POST',':batchUpdate',json={'requests':reqs});actual=transport.call_args.kwargs['json']['requests']
        updates=[r['updateCells'] for r in actual if 'updateCells' in r]
        num=next(x for x in updates if x['start']['columnIndex']==physical[3])
        self.assertEqual(num['rows'][0]['values'][0]['userEnteredValue']['formulaValue'],f'={column(physical[3])}5+1')
        self.assertTrue(all(r['copyPaste']['source']['startColumnIndex'] in physical for r in actual if 'copyPaste' in r))
        self.assertTrue(any('userEnteredFormat.numberFormat' in x['fields'] for x in updates))

    def test_family_defaults_fill_new_draft_and_keep_manual_document_edits(self):
        config={'recipient':'전체 담당','ledger_connections':{'outgoing:공문':{'defaults':{'receiver':'서울시청','sender':'공문 담당'}},'outgoing:업무연락':{'defaults':{'receiver':'기초센터','sender':'업무 담당','method':'FAX'}},'stamp:공문':{'defaults':{'department':'편의증진부','applicant':'','person':''}},'stamp:업무연락':{'defaults':{'department':'업무 부서','person':''}}}}
        out=outgoing.defaults({'title':'제목'},config)
        self.assertEqual((out['receiver'],out['sender'],out['stamp']['applicant']),('서울시청','공문 담당','공문 담당'))
        out['receiver']='이번만 다른 기관';out['ledger_type']='업무연락';apply_outgoing(out,config)
        self.assertEqual((out['receiver'],out['sender'],out['method']),('이번만 다른 기관','업무 담당','FAX'))
        self.assertEqual(out['stamp']['department'],'업무 부서')
        same=outgoing.defaults({'title':'제목','outgoing':out},config)
        self.assertEqual(same['receiver'],'이번만 다른 기관')
        out['sheet_attempted']=True;apply_outgoing(out,config,True);self.assertEqual(out['receiver'],'이번만 다른 기관')

    def test_receipt_defaults_and_blank_seal_person_remain_explicit(self):
        config={'recipient':'기본 담당','ledger_connections':{'receipt:처리지침':{'defaults':{'issuer':'중앙센터','recipient':'지침 담당','method':'FAX'}},'stamp:처리지침':{'defaults':{'person':''}}}}
        self.assertEqual(default_values(config,'receipt','처리지침'),{'issuer':'중앙센터','recipient':'지침 담당','method':'FAX'})
        self.assertEqual(default_values(config,'receipt','공문')['recipient'],'기본 담당')
        self.assertEqual(default_values(config,'stamp','처리지침')['person'],'')
        config['ledger_connections']['outgoing:공문']={'defaults':{'sender':''}}
        self.assertEqual(outgoing.defaults({'title':'제목','recipient':'기존 담당'},config)['sender'],'')


if __name__=='__main__':unittest.main()
