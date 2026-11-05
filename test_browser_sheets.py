from datetime import date
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import core
from browser_sheets import BrowserSession
from folder_naming import child_folders,copy_to_subfolders


class UI:
    def __init__(self,rows):self.rows=rows;self.writes=[];self.fail=None
    def tab(self,name):self.name=name
    def close(self):pass
    def read(self,range_):
        if re.fullmatch(r"A:[HK]",range_):return [r.copy() for r in self.rows]
        m=re.fullmatch(r"([A-K])(\d+):([A-K])(\d+)",range_)
        if not m:raise ValueError(range_)
        return [r[ord(m[1])-65:ord(m[3])-64] for r in self.rows[int(m[2])-1:int(m[4])]]
    def write(self,cell,value):
        self.writes.append((cell,value))
        if self.fail and len(self.writes)==self.fail:raise OSError("connection interrupted")
        m=re.fullmatch(r"([A-K])(\d+)",cell);col=ord(m[1])-65;row=int(m[2])-1
        if value.startswith("="):
            f=re.fullmatch(r"=([A-K])(\d+)\+1",value)
            value=str(int(self.rows[int(f[2])-1][ord(f[1])-65])+1)
        elif value.startswith("'"):value=value[1:]
        self.rows[row][col]=value


class BrowserTests(unittest.TestCase):
    def test_same_title_and_shared_attachment_get_a_new_row_then_retry_keeps_that_row(self):
        self.job.update(title='같은 공문 제목',files=[{'hash':'body-one','path':'first.pdf'},{'hash':'shared','path':'common.xlsx'}])
        self.assertEqual(self.owner.register(self.job,self.store),(6,'21'))
        second={'hash':'new-case','files':[{'hash':'body-two','path':'second.pdf'},{'hash':'shared','path':'common.xlsx'}],'date':self.job['date'],'title':self.job['title'],'issuer':'중앙센터','recipient':'담당자','state':'확인 필요'}
        self.assertEqual(self.owner.register(second,self.store),(7,'22'))
        self.assertEqual(self.ui.rows[5][3],'21');count=len(self.ui.writes)
        self.assertEqual(self.owner.register(second,self.store),(7,'22'));self.assertEqual(len(self.ui.writes),count)
        self.assertNotEqual(self.job['receipt_registration_markers'],second['receipt_registration_markers'])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.owner=core.Sheets.__new__(core.Sheets);self.owner.tab=core.TAB
        rows=[[""]*8 for _ in range(7)]
        rows[2]=["연번","일자","문서","번호","발신처","제목","담당자","방법"]
        rows[4]=["1","2026-11-01","서울센터2026-","20","서울시청","기존 제목","담당자","Email"]
        self.ui=UI(rows)
        self.session=BrowserSession({"root":str(self.base),"spreadsheet_id":"test-id"},self.owner,ui=self.ui)
        self.owner.browser=self.session
        self.store=core.Store(self.base/"history.sqlite");self.addCleanup(self.store.db.close)
        self.job={"hash":"abc","files":[{"hash":"abc","path":"not-used.pdf"}],"date":"2026-11-02","title":"새 공문","issuer":"중앙센터","recipient":"담당자","state":"확인 필요"}

    def test_ui_receipt_registration_and_shared_journal_deduplication(self):
        row,number=self.owner.register(self.job,self.store)
        self.assertEqual((row,number),(6,"21"))
        self.assertEqual(self.ui.rows[5][5],"새 공문")
        count=len(self.ui.writes)
        self.assertEqual(self.owner.register(self.job,self.store),(6,"21"))
        self.assertEqual(count,len(self.ui.writes))
        self.assertTrue(list((self.base/".수발신-브라우저이력").glob("*.json")))

    def test_partial_write_recovers_same_row_without_duplicate(self):
        self.ui.fail=4
        with self.assertRaises(OSError):self.owner.register(self.job,self.store)
        self.assertTrue(self.job["sheet_attempted"])
        self.ui.fail=None
        self.assertEqual(self.owner.register(self.job,self.store),(6,"21"))
        self.assertEqual(self.ui.rows[6][5],"")
        self.assertEqual(sum(1 for cell,value in self.ui.writes if cell=="A6"),1)

    def test_changed_pending_row_refuses_recovery(self):
        self.ui.fail=4
        with self.assertRaises(OSError):self.owner.register(self.job,self.store)
        self.ui.rows[5][5]="다른 사람이 입력한 문서";self.ui.fail=None
        count=len(self.ui.writes)
        with self.assertRaisesRegex(ValueError,"다른 내용"):self.owner.register(self.job,self.store)
        self.assertEqual(len(self.ui.writes),count)

    def test_added_attachments_keep_original_uncertain_transaction_markers(self):
        self.ui.fail=4
        with self.assertRaises(OSError):self.owner.register(self.job,self.store)
        self.job['receipt_marker_files']=[dict(f) for f in self.job['files']]
        self.job['files'].append({'hash':'added-file','path':'extra.pdf'})
        self.store.put(self.job);self.ui.fail=None
        self.assertEqual(self.owner.register(self.job,self.store),(6,'21'))
        self.assertEqual(self.ui.rows[6][5],'')
        self.assertEqual(sum(1 for cell,value in self.ui.writes if cell=='A6'),1)

    def test_connection_snapshot_never_resumes_pending_writes(self):
        self.ui.fail=4
        with self.assertRaises(OSError):self.owner.register(self.job,self.store)
        self.ui.fail=None;count=len(self.ui.writes)
        self.owner.snapshot()
        self.assertEqual(count,len(self.ui.writes))

    def test_validation_before_first_write_resets_attempt_flag(self):
        self.job["title"]="여러\n줄"
        with self.assertRaises(core.RejectedWrite):self.owner.register(self.job,self.store)
        self.assertFalse(self.job["sheet_attempted"])
        self.assertFalse(self.ui.writes)

    def test_literal_formula_like_title_is_not_executed(self):
        self.job["title"]="=SUM(A1:A9)"
        self.owner.register(self.job,self.store)
        self.assertEqual(self.ui.rows[5][5],self.job["title"])
        self.assertIn(("F6","'=SUM(A1:A9)"),self.ui.writes)

    def test_completed_row_manual_edits_dont_block_future_registrations(self):
        self.owner.register(self.job,self.store)
        self.ui.rows[5][6]="담당자 수기 변경"
        self.assertEqual(self.owner.register(self.job,self.store),(6,"21"))
        self.ui.rows[5][5]="제목 변경"
        with self.assertRaisesRegex(ValueError,"불확실"):self.owner.register(self.job,self.store)

    def test_child_selection_compacts_numbers_for_three_agencies(self):
        for agency,prefix in (("기초센터 공문","기초"),("중앙센터 공문","중앙"),("서울시청 공문","시청")):
            self.assertEqual(list(child_folders(agency,["incoming","reply"]).values()),[f"1.{prefix}공문",f"2.{prefix}회신"])
            self.assertEqual(list(child_folders(agency,["outgoing","reply"]).values()),["1.센터발송",f"2.{prefix}회신"])
            self.assertEqual(len(child_folders(agency,["incoming","outgoing","reply"])),3)
            self.assertEqual(child_folders(agency,[]),{})

    def test_copies_to_selected_incoming_child_and_retry_doesnt_renumber(self):
        parent=self.base/"0.시청공문";parent.mkdir()
        source=self.base/"첨부.pdf";source.write_bytes(b"pdf attachment")
        job={"id":"id","title":"선택 제목","date":"2026-11-02","files":[{"path":str(source),"hash":core.digest(source)}],
             "routing":{"agency":"서울시청 공문","targets":[{"relative":"0.시청공문","template":"{number:03d}. {title}"}],"children":["incoming","reply"]}}
        copy_to_subfolders(job,self.base);copy_to_subfolders(job,self.base)
        folder=parent/"001. 선택 제목"
        self.assertEqual((folder/"1.시청공문"/"첨부.pdf").read_bytes(),source.read_bytes())
        self.assertTrue((folder/"2.시청회신").is_dir())
        self.assertFalse((folder/"3.시청회신").exists())
        self.assertFalse((parent/"002. 선택 제목").exists())


if __name__=="__main__":unittest.main()
