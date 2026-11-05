from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import core
import file_rollback as rollback
from folder_naming import AGENCIES


class FileRollbackTests(unittest.TestCase):
    def test_original_zip_copy_and_rollback_preserve_all_contents_and_receipt_number(self):
        import zipfile
        archive=self.base/'bundle.zip'
        with zipfile.ZipFile(archive,'w') as zipped:zipped.writestr('공문.pdf',b'document');zipped.writestr('사진.jpg',b'original picture')
        original=archive.read_bytes();self.job['archive_files']=[{'path':str(archive),'hash':core.digest(archive)}]
        folder=self.save();saved=folder/'1.중앙공문'/'bundle.zip';self.assertEqual(saved.read_bytes(),original)
        rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertFalse(folder.exists());self.assertEqual(archive.read_bytes(),original);self.assertEqual(self.job['number'],'581')
    def test_reordered_custom_child_folder_is_copied_and_rolled_back_with_number_preserved(self):
        target=self.job['routing']['targets'][0]
        target['children']=['reply','custom:검토자료','incoming']
        target['child_order']=['reply','custom:검토자료','incoming','outgoing']
        folder=self.save()
        self.assertTrue((folder/'1.중앙회신').is_dir());self.assertTrue((folder/'2.검토자료').is_dir())
        self.assertIn('3.중앙공문',self.job['destinations'][0])
        rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertFalse(folder.exists());self.assertEqual(self.job['number'],'581');self.assertTrue(self.source.is_file())
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'shared';self.root.mkdir()
        for name in AGENCIES['중앙센터 공문']:(self.root/name).mkdir()
        self.source=self.base/'original.pdf';self.source.write_bytes(b'original attachment')
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.config={'root':str(self.root),'recipient':'담당자'}
        self.job={'hash':core.digest(self.source),'id':'receipt-fixture','state':'확인 필요','files':[{'path':str(self.source),'hash':core.digest(self.source)}],'title':'공문 제목','category':'중앙센터 공문','receipt_type':'공문','recipient':'담당자','date':'2026-11-03','issuer':'중앙센터','number':'','routing':{'agency':'중앙센터 공문','targets':[{'relative':'0.중앙센터 공문','template':'{number:03d}. {title}'}],'children':['incoming','outgoing','reply']}}
        self.factory=Mock()
        def register(job,store):job['sheet_attempted']=True;store.put(job);return 6,'581'
        self.factory.return_value.register.side_effect=register

    def save(self):
        core.process(self.job,self.config,self.store,sheets_factory=self.factory)
        self.folder=Path(self.job['storage_roots'][0]['path'])
        return self.folder

    def test_multiple_city_destinations_create_their_own_children_and_rollback(self):
        from folder_naming import child_kinds
        targets=[]
        for relative in ('1.건축허가&사업계획승인','2.사용승인&사용검사'):
            (self.root/relative).mkdir();targets.append({'relative':relative,'template':'{number:03d}. {title}','children':list(child_kinds(relative))})
        self.job['category']='서울시청 공문';self.job['routing']={'agency':'서울시청 공문','targets':targets,'children':['incoming','reply']}
        self.save();folders=[Path(r['path']) for r in self.job['storage_roots']]
        self.assertTrue((folders[0]/'2.협의도면').is_dir());self.assertTrue((folders[0]/'3.시청회신').is_dir())
        self.assertTrue((folders[1]/'3.현장사진').is_dir());self.assertTrue((folders[1]/'4.시청회신').is_dir())
        self.assertTrue(all('1.시청공문' in p for p in self.job['destinations']))
        rollback.execute(self.job,self.config,self.store,'receipt');self.assertTrue(all(not f.exists() for f in folders));self.assertEqual(self.job['number'],'581')

    def test_rollback_removes_case_folder_but_keeps_source_and_all_ledger_identity(self):
        folder=self.save();identity={k:deepcopy(self.job[k]) for k in ('number','row','title','sheet_attempted','receipt_done','receipt_tab','id')}
        rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertFalse(folder.exists());self.assertEqual(self.source.read_bytes(),b'original attachment')
        self.assertTrue(Path(self.job['rollback_history'][-1]['moves'][0]['backup']).is_dir())
        self.assertEqual(identity,{k:self.job[k] for k in identity});self.assertTrue(self.job['files_rolled_back'])
        self.assertFalse(rollback.has_files(self.job,'receipt'))

    def test_resave_after_rollback_uses_existing_sheet_number_without_any_sheet_calls(self):
        self.save();rollback.execute(self.job,self.config,self.store,'receipt')
        self.job['routing']['folder_title']='새 저장 폴더 제목'
        forbidden=Mock(side_effect=AssertionError('Must never register again'))
        core.process(self.job,self.config,self.store,sheets_factory=forbidden,outgoing_factory=forbidden,stamp_factory=forbidden)
        forbidden.assert_not_called();self.assertEqual(self.job['number'],'581')
        self.assertTrue(all(Path(p).is_file() for p in self.job['destinations']))
        self.assertIn('새 저장 폴더 제목',self.job['destinations'][0]);self.assertFalse(self.job['files_rolled_back'])

    def test_file_only_resave_does_not_finish_pending_outgoing_or_stamp_registration(self):
        self.save();rollback.execute(self.job,self.config,self.store,'receipt')
        self.job['outgoing']={'enabled':True,'complete':False,'stamp':{'complete':False},'number':'12','receiver':''}
        expected=deepcopy(self.job['outgoing']);forbidden=Mock(side_effect=AssertionError('No outgoing or stamp writes'))
        core.process(self.job,self.config,self.store,sheets_factory=forbidden,outgoing_factory=forbidden,stamp_factory=forbidden)
        self.assertEqual(self.job['outgoing'],expected);forbidden.assert_not_called()

    def test_explicit_outgoing_registration_can_continue_after_file_restore(self):
        from outgoing import defaults
        self.save();rollback.execute(self.job,self.config,self.store,'receipt')
        self.job['outgoing']={**defaults(self.job,self.config),'enabled':True,'receiver':'수신 기관'}
        forbidden=Mock(side_effect=AssertionError('No second receipt registration'))
        outgoing=Mock();outgoing.return_value.register.return_value=(7,'12')
        stamp=Mock();stamp.return_value.register.return_value=(8,'7')
        core.process(self.job,{**self.config,'_register_outgoing_after_restore':True},self.store,sheets_factory=forbidden,outgoing_factory=outgoing,stamp_factory=stamp)
        forbidden.assert_not_called();outgoing.return_value.register.assert_called_once();stamp.return_value.register.assert_called_once()
        self.assertEqual(self.job['number'],'581');self.assertEqual(self.job['outgoing']['number'],'12')

    def test_extra_or_modified_files_block_entire_rollback(self):
        folder=self.save();extra=folder/'manual-note.txt';extra.write_text('user work')
        with self.assertRaisesRegex(ValueError,'추가·수정'):rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertTrue(folder.exists());self.assertTrue(extra.exists());self.assertNotIn('rollback_pending',self.job)
        extra.unlink();Path(self.job['destinations'][0]).write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError,'추가·수정'):rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertTrue(folder.exists())

    def test_old_version_case_is_identified_by_marker_and_attachment_hash(self):
        folder=self.save();self.job.pop('storage_roots');self.job.pop('file_artifacts')
        rollback.execute(self.job,self.config,self.store,'receipt');self.assertFalse(folder.exists())

    def test_missing_download_uses_verified_backup_for_file_only_resave(self):
        self.save();self.source.unlink()
        rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertTrue(Path(self.job['files'][0]['path']).is_file())
        forbidden=Mock(side_effect=AssertionError('No sheet access'))
        core.process(self.job,self.config,self.store,sheets_factory=forbidden)
        self.assertTrue(Path(self.job['destinations'][0]).is_file());forbidden.assert_not_called()

    def test_legacy_common_file_without_creation_record_is_not_removed(self):
        file=self.root/'original.pdf';file.write_bytes(self.source.read_bytes())
        self.job.update(destinations=[str(file)],files_started=True)
        with self.assertRaisesRegex(ValueError,'공용 파일'):rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertTrue(file.exists())

    def test_preflight_checks_every_target_before_any_move(self):
        self.job['routing']['targets'].append({'relative':'8.실태조사','template':'{number:03d}. {title}'})
        self.save();roots=[Path(r['path']) for r in self.job['storage_roots']]
        (roots[1]/'manual-file').write_bytes(b'keep')
        with self.assertRaises(ValueError):rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertTrue(all(p.is_dir() for p in roots))

    def test_partial_move_can_resume_without_changing_ledger_or_duplicating_backup(self):
        self.job['routing']['targets'].append({'relative':'8.실태조사','template':'{number:03d}. {title}'})
        self.save();rename=Path.rename;calls=[0]
        def failing(path,target):
            calls[0]+=1
            if calls[0]==2:raise OSError('fixture disconnect')
            return rename(path,target)
        with patch.object(Path,'rename',new=failing),self.assertRaises(OSError):rollback.execute(self.job,self.config,self.store,'receipt')
        saved=self.store.get(self.job['hash']);self.assertIn('rollback_pending',saved)
        rollback.execute(saved,self.config,self.store,'receipt')
        self.assertEqual(len(saved['rollback_history'][-1]['moves']),2);self.assertEqual(saved['number'],'581')

    def outgoing(self,base=None):
        from outgoing_folders import prepare
        from outgoing import defaults
        base=base or self.root/'시행문';base.mkdir(exist_ok=True)
        out=defaults(self.job,self.config);out.update(id='out-fixture',enabled=True,complete=True,sheet_attempted=True,number='12',receiver='수신 기관',template_id='1')
        out['stamp'].update(complete=True,sheet_attempted=True,number='10')
        out['folder_plan']={'base':str(base),'template':'{number:03d}. {title}','folder_title':'발신 폴더','date':'2026-11-03','agency':'중앙센터 공문','children':['outgoing','reply'],'save_in':'outgoing'}
        self.job['outgoing']=out;save=prepare(self.job,self.config,self.store)
        file=save/'012. outgoing.hwp';file.write_bytes(b'generated hwp fixture')
        out.update(hwp_path=str(file),hwp_saved_number='12',hwp_created_sha256=core.digest(file))
        rollback.track_file(self.job,file,core.digest(file),'outgoing');self.store.put(self.job)
        return file

    def test_outgoing_only_rollback_keeps_receipt_files_and_both_ledgers(self):
        self.save();file=self.outgoing();receipt=list(self.job['destinations'])
        rollback.execute(self.job,self.config,self.store,'outgoing')
        self.assertFalse(file.exists());self.assertTrue(all(Path(p).exists() for p in receipt))
        self.assertEqual(self.job['number'],'581');self.assertEqual(self.job['outgoing']['number'],'12')
        self.assertTrue(self.job['outgoing']['stamp']['complete']);self.assertFalse(rollback.has_files(self.job,'outgoing'))

    def test_modified_hwp_and_legacy_hwp_are_protected(self):
        self.save();file=self.outgoing();file.write_bytes(b'user edited HWP')
        with self.assertRaises(ValueError):rollback.execute(self.job,self.config,self.store,'outgoing')
        self.assertTrue(file.exists());self.job['outgoing'].pop('hwp_created_sha256')
        with self.assertRaisesRegex(ValueError,'이전 버전'):rollback.execute(self.job,self.config,self.store,'outgoing')

    def test_nested_outgoing_backup_does_not_block_later_receipt_rollback(self):
        folder=self.save();self.outgoing(folder/'2.센터발송')
        rollback.execute(self.job,self.config,self.store,'outgoing')
        backup=Path(self.job['rollback_history'][-1]['moves'][0]['backup'])
        self.assertFalse(backup.is_relative_to(folder))
        rollback.execute(self.job,self.config,self.store,'receipt')
        self.assertFalse(folder.exists());self.assertTrue(backup.exists())


if __name__=='__main__':unittest.main()
