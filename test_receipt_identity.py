from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape
import core


class ReceiptIdentityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.store=core.Store(self.root/'history.sqlite');self.addCleanup(self.store.db.close)

    def document(self,name,body):
        path=self.root/name
        text='서울특별시장\n제목 같은 제목\n1. '+body
        with zipfile.ZipFile(path,'w') as zipped:
            zipped.writestr('Contents/section0.xml','<section xmlns:h="urn:test">'+''.join(f'<h:p><h:t>{escape(line)}</h:t></h:p>' for line in text.splitlines())+'</section>')
        return path

    def test_same_title_different_body_is_new_without_inheriting_a_number(self):
        first,_=core.ingest(self.document('first.hwpx','첫 번째 내용'),self.store)
        first.update(receipt_done=True,state='완료',number='350');self.store.put(first)
        second,new=core.ingest(self.document('second.hwpx','다른 공문 내용'),self.store)
        self.assertTrue(new);self.assertEqual(first['title'],second['title'])
        self.assertNotEqual(first['hash'],second['hash']);self.assertEqual(second['number'],'');self.assertNotIn('receipt_done',second)
        self.assertEqual(self.store.get(first['hash'])['number'],'350')

    def test_legacy_shared_file_does_not_reuse_a_number_for_a_new_group(self):
        job,_=core.ingest(self.document('current.hwpx','새 내용'),self.store)
        job.update(recipient='담당자',issuer='서울시청')
        job['files'].append({'hash':'shared-attachment','path':'not-read.xlsx'})
        sheets=core.Sheets.__new__(core.Sheets);sheets.tab=core.TAB
        rows=[[],[],['연번','일자','문서','번호','발신처','제목'],[],[],['1',job['date'],'서울센터2026-','350','서울시청',job['title'],'담당자','Email'],['2',job['date'],'서울센터2026-','351']]
        sheets.snapshot=lambda:({'sheetId':0,'gridProperties':{'rowCount':1000}},rows,{'SDR:shared-attachment':6})
        writes=[]
        def request(method,suffix='',**kwargs):
            if method=='POST':writes.append(kwargs)
            return {'values':[['351','서울시청',job['title']]]}
        sheets.request=request
        self.assertEqual(sheets.register(job,self.store),(7,'351'));self.assertEqual(len(writes),1)

    def test_legacy_uncertain_retry_keeps_original_transaction_markers(self):
        job={'files':[{'hash':'body'},{'hash':'common'}],'sheet_attempted':True}
        self.assertEqual(core.receipt_markers(job),['SDR:body','SDR:common'])
        self.assertNotIn('receipt_registration_markers',job)
