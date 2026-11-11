from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import re,tempfile,unittest
from unittest.mock import Mock,patch
from core import Store,receipt_markers
from browser_journal import Journal
import registration_cancel as cancel


class CancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name);root=base/'shared';root.mkdir()
        self.config={'root':str(root),'_program_dir':str(base/'Program'),'auth_mode':'browser'}
        self.store=Store(base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.job={'hash':'fixture','id':'fixture','title':'제목','state':'완료','receipt_type':'공문','receipt_done':True,'sheet_attempted':True,'number':'350','row':6,
                  'files':[{'hash':'source-fixture','path':str(base/'original.pdf')}],'outgoing':{'id':'out-fixture','enabled':True,'ledger_type':'공문','title':'제목','number':'452','file_number':'452','document_number':'서울센터2026-452','complete':True,'sheet_attempted':True,'folder_plan':None,
                                      'stamp':{'number':'445','complete':True,'sheet_attempted':True}}}
        self.rows={};self.writes=[];self.fail_at=None
        for scope in ('receipt','outgoing','stamp'):
            width=8 if scope=='receipt' else 11
            before=['']*width;before[0]='445';after=before.copy()
            if scope=='receipt':before[2:4]=['서울센터2026-','350'];after=before.copy();after[1]='2026-11-10';after[4:8]=['제목','중앙센터','신익재','Email']
            elif scope=='outgoing':before[5:7]=['서울센터2026-','452'];after=before.copy();after[1]='2026-11-10';after[2]='○';after[7:11]=['제목','서울시청','신익재','Email']
            else:after[1:]=['2026-11-10','○','','','서울센터2026-452','제목','서울시청','편의증진부','신익재','']
            self.rows[scope]=[['']*width for _ in range(5)]+[after.copy()]
            entry={'sid':'fixture-sheet','tab':scope,'row':6,'before':before,'after':after,'markers':cancel.markers(self.job,scope),'complete':True}
            Journal(self.config).archive(None,entry)
        self.store.put(self.job)

    def factory(self,config,target):
        rows=self.rows[target['scope']]
        def write(cell,value):
            if self.fail_at==len(self.writes):raise OSError('fixture interrupted')
            col=ord(cell[0])-65;row=int(cell[1:]);rows[row-1][col]=value.lstrip("'");self.writes.append((target['scope'],row,col,value))
        browser=SimpleNamespace(ui=SimpleNamespace(write=write),getrows=lambda address:[rows[int(re.search(r'\d+',address).group())-1].copy()],close=Mock())
        return SimpleNamespace(snapshot=lambda:(None,deepcopy(rows),{}),header_row=2,last_column=chr(64+len(rows[0])),browser=browser)

    def test_outgoing_cancels_stamp_and_outgoing_cells_preserving_receipt_and_numbers(self):
        original=deepcopy(self.rows);old_id=self.job['outgoing']['id']
        cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertEqual(self.rows['receipt'],original['receipt'])
        for scope in ('stamp','outgoing'):
            for col in cancel.columns(scope,'공문'):self.assertEqual(self.rows[scope][5][col],'')
        for col in (0,5,6):self.assertEqual(self.rows['outgoing'][5][col],original['outgoing'][5][col])
        self.assertEqual(self.job['number'],'350');self.assertTrue(self.job['receipt_done'])
        self.assertNotIn('number',self.job['outgoing']);self.assertNotEqual(old_id,self.job['outgoing']['id'])
        self.assertEqual(sum(e.get('cancelled',False) for e in Journal(self.config).entries()),2)

    def test_receipt_cancels_all_three_ledgers_and_mints_new_receipt_identity(self):
        old=receipt_markers(self.job)
        cancel.execute(self.job,self.config,self.store,'receipt',self.factory)
        self.assertNotIn('number',self.job);self.assertNotIn('receipt_done',self.job)
        self.assertNotEqual(receipt_markers(self.job),old)
        for scope in self.rows:
            for col in cancel.columns(scope,'공문'):self.assertEqual(self.rows[scope][5][col],'')

    def test_foreign_edits_block_every_sheet_and_file_change(self):
        self.rows['outgoing'][5][7]='다른 사람이 수정한 제목'
        with patch('file_rollback.execute') as files:
            with self.assertRaisesRegex(ValueError,'내용이 바뀌'):cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        files.assert_not_called();self.assertEqual(self.writes,[]);self.assertTrue(self.job['outgoing']['complete'])

    def test_partial_cancel_resumes_without_duplicate_writes_or_old_number(self):
        self.fail_at=3
        with self.assertRaises(OSError):cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        first=self.writes.copy();self.assertIn('registration_cancel_pending',self.job)
        self.fail_at=None;cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertEqual(self.writes[:3],first);self.assertEqual(len(self.writes),len(set(self.writes)))
        self.assertNotIn('registration_cancel_pending',self.job);self.assertNotIn('number',self.job['outgoing'])

    def test_shifted_row_is_found_but_duplicate_identity_is_rejected(self):
        self.rows['stamp'].insert(4,['']*11)
        cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertTrue(any(s=='stamp' and row==7 for s,row,_,_ in self.writes))

    def test_missing_ownership_evidence_keeps_rows(self):
        self.job['outgoing']['id']='unknown'
        with self.assertRaisesRegex(ValueError,'등록 확인 이력'):cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertEqual(self.writes,[])

    def test_direct_hwp_without_folder_plan_is_removed_only_when_owned(self):
        from core import digest
        from file_rollback import track_file
        root=Path(self.config['root']);source=root.parent/'download.hwp';source.write_bytes(b'original')
        saved=root/'452.hwp';saved.write_bytes(b'generated')
        self.job['outgoing'].update(hwp_path=str(saved),hwp_created_sha256=digest(saved),save_folder=str(root))
        track_file(self.job,str(saved),digest(saved),'outgoing');self.store.put(self.job)
        cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertFalse(saved.exists());self.assertEqual(source.read_bytes(),b'original')
        self.assertEqual(self.job['number'],'350')

    def test_changed_hwp_is_backed_up_and_sheet_cancellation_completes(self):
        from core import digest
        from file_rollback import track_file
        saved=Path(self.config['root'])/'452.hwp';saved.write_bytes(b'generated')
        self.job['outgoing'].update(hwp_path=str(saved),hwp_created_sha256=digest(saved),save_folder=self.config['root'])
        track_file(self.job,str(saved),digest(saved),'outgoing');saved.write_bytes(b'user edits')
        cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertFalse(saved.exists());self.assertTrue(self.writes)
        edited=self.job['edited_hwp_backups'][-1];backup=Path(edited['backup'])
        self.assertEqual(backup.read_bytes(),b'user edits');self.assertTrue(backup.resolve().is_relative_to(Path(self.config['_program_dir']).resolve()))
        self.assertIn(str(backup),self.job['reason']);self.assertNotIn('number',self.job['outgoing'])
        from program_storage import cleanup_rollbacks
        self.job['outgoing']['files_rolled_back']=False;self.store.put(self.job)
        cleanup_rollbacks(self.job,self.config,self.store);self.assertEqual(backup.read_bytes(),b'user edits')

    def test_backup_failure_or_more_edits_keep_sheet_rows_and_latest_file(self):
        from core import digest
        from file_rollback import track_file
        from program_storage import copy_verified
        saved=Path(self.config['root'])/'452.hwp';saved.write_bytes(b'generated')
        self.job['outgoing'].update(hwp_path=str(saved),hwp_created_sha256=digest(saved),save_folder=self.config['root'])
        track_file(self.job,str(saved),digest(saved),'outgoing');saved.write_bytes(b'user edits')
        def changed_after_backup(*args):copy_verified(*args);saved.write_bytes(b'more user edits')
        with patch('program_storage.copy_verified',side_effect=changed_after_backup),self.assertRaises(ValueError):
            cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertEqual(self.writes,[]);self.assertEqual(saved.read_bytes(),b'more user edits')
        backup=Path(self.job['rollback_pending']['edited_files'][0]['backup']);self.assertEqual(backup.read_bytes(),b'user edits')

    def test_untracked_changed_file_still_blocks_sheet_cancellation(self):
        self.job['outgoing']['hwp_path']=str(Path(self.config['root'])/'old.hwp')
        Path(self.job['outgoing']['hwp_path']).write_bytes(b'unproven old file')
        with self.assertRaisesRegex(ValueError,'이전 버전'):cancel.execute(self.job,self.config,self.store,'outgoing',self.factory)
        self.assertEqual(self.writes,[]);self.assertEqual(Path(self.job['outgoing']['hwp_path']).read_bytes(),b'unproven old file')


if __name__=='__main__':unittest.main()
