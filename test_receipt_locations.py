from pathlib import Path
import tempfile
import unittest
import core
import folder_naming as naming


class ReceiptLocationTests(unittest.TestCase):
    def test_year_storage_reuses_existing_file_without_owning_or_removing_it(self):
        from file_rollback import execute
        relative=str(Path('5.보건복지부 및 중앙처리지침')/'0. 보건복지부 지침')
        year=self.root/relative/'2026';year.mkdir(parents=True)
        job=self.job();(year/'공문.pdf').write_bytes(Path(job['files'][0]['path']).read_bytes())
        job['routing']={'agency':'중앙센터 공문','targets':[{'relative':relative,'template':'{title}','children':[],
            'storage_mode':'year','year_folder':'2026'}]}
        job['destinations']=naming.copy_to_subfolders(job,self.root)
        execute(job,{'root':str(self.root)},self.store,'receipt')
        self.assertTrue((year/'공문.pdf').is_file());self.assertTrue(year.is_dir())

    def test_year_storage_rejects_traversal_and_non_guideline_locations(self):
        for target in ({'relative':'0.중앙센터 공문','storage_mode':'year','year_folder':'2026'},
                       {'relative':str(Path('5.보건복지부 및 중앙처리지침')/'0. 보건복지부 지침'),'storage_mode':'year','year_folder':'../2026'}):
            with self.assertRaises(ValueError):naming.validate_target_mode(target)

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.store=core.Store(self.root/'history.sqlite');self.addCleanup(self.store.db.close)

    def job(self):
        path=self.root/'공문.pdf';path.write_bytes(b'local fixture')
        return {'id':'year-case','hash':core.digest(path),'files':[{'path':str(path),'hash':core.digest(path)}],
                'title':'대장 제목','date':'2026-11-05','category':'중앙센터 공문','number':'','state':'확인 필요'}

    def test_separate_case_title_and_shared_year_folder_copy_and_rollback(self):
        from file_rollback import execute
        shared=self.root/'공유';central=shared/'0.중앙센터 공문';central.mkdir(parents=True)
        relative=str(Path('5.보건복지부 및 중앙처리지침')/'1. 중앙지침(처리지침, 업무연락 등)')
        year=shared/relative/'2026년';year.mkdir(parents=True);(year/'기존.txt').write_text('keep')
        job=self.job();job['id']='year-case';job['routing']={'agency':'중앙센터 공문','targets':[
            {'relative':'0.중앙센터 공문','template':'{title}','folder_title':'중앙용 제목','children':['incoming','outgoing','reply']},
            {'relative':relative,'template':'{title}','folder_title':'지침용 제목','storage_mode':'year','year_folder':'2026년','children':[]}]}
        job['destinations']=naming.copy_to_subfolders(job,shared);self.store.put(job)
        self.assertTrue((central/'중앙용 제목'/'1.중앙공문'/'공문.pdf').is_file())
        self.assertTrue((year/'공문.pdf').is_file());self.assertFalse((year/'지침용 제목').exists())
        execute(job,{'root':str(shared)},self.store,'receipt')
        self.assertFalse((year/'공문.pdf').exists());self.assertEqual((year/'기존.txt').read_text(),'keep')
        self.assertTrue(Path(job['files'][0]['path']).is_file())

