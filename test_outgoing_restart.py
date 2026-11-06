from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import core
import outgoing
from outgoing_restart import restart


class OutgoingRestartTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.file=self.base/'registered.hwp';self.file.write_bytes(b'keep saved outgoing document')
        self.job={'hash':'receipt','title':'접수 제목','receipt_done':True,'number':'355','row':400,'state':'발신 완료·직인 등록 대기','files':[{'path':str(self.file),'hash':'source'}],
                  'outgoing':{'id':'cancelled-attempt','enabled':True,'number':'444','document_number':'서울센터2026-444','row':500,'sheet_attempted':True,'complete':True,'template_id':'2','body':'old draft','hwp_path':str(self.file),'folder_plan':{'created':True,'save_path':str(self.base)},'stamp':{'sheet_attempted':True,'row':436}}}
        self.store.put(self.job)

    def test_clears_outgoing_and_stamp_progress_but_preserves_receipt_files_and_audit(self):
        old=deepcopy(self.job);fresh=restart(self.job,{'recipient':'기본 담당자'},self.store,expected_id='cancelled-attempt')
        out=fresh['outgoing'];self.assertNotEqual(out['id'],old['outgoing']['id'])
        for key in ('number','row','document_number','sheet_attempted','complete','hwp_path','folder_plan','template_id'):self.assertNotIn(key,out)
        self.assertEqual(out['body'],'');self.assertEqual(out['title'],'접수 제목');self.assertEqual(out['sender'],'기본 담당자')
        self.assertEqual(out['stamp']['department'],'편의증진부');self.assertEqual(out['stamp']['person'],'')
        for key in ('row','number','sheet_attempted','complete'):self.assertNotIn(key,out['stamp'])
        self.assertEqual(fresh['number'],'355');self.assertEqual(fresh['row'],400);self.assertTrue(fresh['receipt_done'])
        self.assertEqual(fresh['outgoing_attempts'][0]['outgoing'],old['outgoing']);self.assertEqual(self.job,old)
        self.assertEqual(self.file.read_bytes(),b'keep saved outgoing document');self.assertEqual(self.store.get('receipt'),fresh)

    def test_delete_hides_only_outgoing_and_restarting_generates_another_id(self):
        hidden=restart(self.job,{},self.store,hide=True)
        self.assertTrue(hidden['outgoing_list_hidden']);self.assertNotIn('history_hidden',hidden)
        fresh=restart(hidden,{},self.store)
        self.assertNotIn('outgoing_list_hidden',fresh);self.assertNotEqual(hidden['outgoing']['id'],fresh['outgoing']['id'])
        self.assertEqual(fresh['number'],'355');self.assertTrue(self.file.is_file())

    def test_stale_confirmation_cannot_cancel_a_new_attempt(self):
        before=self.store.get('receipt')
        with self.assertRaisesRegex(ValueError,'변경'):restart(self.job,{},self.store,expected_id='another-id')
        self.assertEqual(before,self.store.get('receipt'))

    def test_new_registration_reads_latest_ledger_instead_of_recovering_cancelled_number(self):
        fresh=restart(self.job,{'recipient':'담당자'},self.store);out=fresh['outgoing'];out.update(enabled=True,receiver='수신 기관',sender='담당자')
        sheet=outgoing.OutgoingSheets.__new__(outgoing.OutgoingSheets)
        sheet.tab=outgoing.OUTGOING_TAB;sheet.compact=False;sheet.prefix_col=5;sheet.number_col=6
        rows=[[],[],[],[],['430','2026-11-06','○','','','서울센터2026-','443','앞 공문'],['431','2026-11-06','○','','','서울센터2026-','444','다른 사람이 사용한 공문'],[]]
        sheet.snapshot=lambda:({'sheetId':7,'gridProperties':{'rowCount':1000}},rows,{'SDO:cancelled-attempt':6})
        writes=[]
        def request(method,suffix='',**kwargs):
            if method=='POST':writes.append(kwargs);return {}
            return {'values':[['445','접수 제목']]}
        sheet.request=request
        self.assertEqual(sheet.register(fresh,self.store),(7,'445'))
        self.assertEqual(len(writes),1);self.assertEqual(fresh['outgoing']['number'],'445')
        note=writes[0]['json']['requests'][-1]['updateCells']['rows'][0]['values'][0]['note']
        self.assertIn('SDO:'+out['id'],note);self.assertNotIn('cancelled-attempt',note)
