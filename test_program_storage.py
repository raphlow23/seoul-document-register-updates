from pathlib import Path
import json,sys,tempfile,unittest
from unittest.mock import patch
import core
import program_storage as storage
from file_rollback import inventory
from browser_journal import Journal
from fs_paths import io_path,logical_path


class ProgramStorageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name).resolve()
        self.shared=self.base/'shared';self.shared.mkdir();self.program=self.base/'Program'
        self.config={'root':str(self.shared),'_program_dir':str(self.program),'spreadsheet_id':'fixture'}
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)

    def legacy_job(self):
        folder=self.shared/'.수발신-되돌리기';backup=folder/'case' /('a'*32)/'0'/'case-files';backup.mkdir(parents=True)
        (backup/'document.pdf').write_bytes(b'only source copy')
        job={'hash':'case','id':'case','files':[{'path':str(backup/'document.pdf'),'hash':core.digest(backup/'document.pdf')}],'files_rolled_back':True,
             'rollback_history':[{'id':'a'*32,'scope':'receipt','moves':[{'source':str(self.shared/'old-case'),'backup':str(backup),'backup_root':str(self.shared),'slot':'0','inventory':inventory(backup),'done':True}]}]}
        self.store.put(job);return folder,backup,job

    def test_frozen_root_is_executable_folder_not_extracted_or_profile_folder(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.program/'app.exe')),patch.object(sys,'_MEIPASS',str(self.base/'extract'),create=True):
            self.assertEqual(storage.program_root({'_auth_data_dir':str(self.base/'profile')}),self.program)

    def test_failed_cross_volume_copy_keeps_source_and_cannot_delete_it(self):
        src=self.shared/'case';src.mkdir();(src/'a.pdf').write_bytes(b'keep original');before=inventory(src)
        with patch.object(storage.shutil,'copy2',side_effect=OSError('copy disconnected')),self.assertRaises(OSError):storage.copy_verified(src,self.program/'backup',before)
        self.assertEqual(inventory(src),before)
        storage.copy_verified(src,self.program/'backup',before);self.assertEqual(inventory(self.program/'backup'),before)

    def test_manually_changed_or_added_files_are_not_removed(self):
        src=self.shared/'case';src.mkdir();(src/'a.pdf').write_bytes(b'original');before=inventory(src)
        storage.copy_verified(src,self.program/'backup',before);(src/'manual.txt').write_text('user addition')
        with self.assertRaises(ValueError):storage.remove_verified(src,before)
        self.assertTrue((src/'a.pdf').is_file());self.assertTrue((src/'manual.txt').is_file())

    def test_existing_shared_backup_moves_and_referenced_copy_survives_until_resave(self):
        legacy,backup,job=self.legacy_job();storage.migrate_rollbacks(self.config,self.store)
        updated=self.store.get('case');move=updated['rollback_history'][0]['moves'][0];local=Path(move['backup'])
        self.assertFalse(legacy.exists());self.assertTrue(local.is_relative_to(self.program))
        self.assertEqual(Path(updated['files'][0]['path']).read_bytes(),b'only source copy')
        storage.cleanup_rollbacks(updated,self.config,self.store);self.assertTrue(local.exists())
        updated['files_rolled_back']=False;storage.cleanup_rollbacks(updated,self.config,self.store);self.assertTrue(local.exists())
        original=self.base/'original.pdf';original.write_bytes(b'only source copy');updated['files'][0]['path']=str(original)
        storage.cleanup_rollbacks(updated,self.config,self.store);self.assertFalse(local.exists());self.assertTrue(original.exists())

    def test_interrupted_migration_keeps_shared_data_and_resumes_from_manifest(self):
        legacy,backup,job=self.legacy_job()
        with patch.object(storage.shutil,'copy2',side_effect=OSError('copy failed')),self.assertRaises(OSError):storage.migrate_rollbacks(self.config,self.store)
        self.assertTrue(legacy.exists());self.assertEqual(self.store.get('case')['files'],job['files'])
        storage.migrate_rollbacks(self.config,self.store);self.assertFalse(legacy.exists())
        self.assertEqual(Path(self.store.get('case')['files'][0]['path']).read_bytes(),b'only source copy')

    def test_long_korean_paths_resume_after_failed_rename_without_losing_source(self):
        legacy=self.shared/'.수발신-되돌리기'
        backup=legacy/('b'*64)/('a'*32)/'0'/('긴공문제목_'*16)
        document=backup/('첨부문서제목_'*18+'.pdf')
        io_path(backup).mkdir(parents=True);io_path(document).write_bytes(b'original long attachment')
        job={'hash':'long','id':'b'*64,'files':[{'path':str(document),'hash':core.digest(document)}],
             'files_rolled_back':True,'rollback_history':[{'id':'a'*32,'scope':'receipt','moves':[
                 {'source':str(self.shared/backup.name),'backup':str(backup),'backup_root':str(self.shared),'slot':'0','inventory':inventory(backup),'done':True}]}]}
        self.store.put(job);real_replace=storage.os.replace
        def interrupted(src,dst):
            if Path(src).suffix=='.part':raise FileNotFoundError(3,'simulated interrupted long rename')
            return real_replace(src,dst)
        with patch.object(storage.os,'replace',side_effect=interrupted),self.assertRaises(FileNotFoundError):
            storage.migrate_rollbacks(self.config,self.store)
        self.assertEqual(io_path(document).read_bytes(),b'original long attachment')
        self.assertEqual(self.store.get('long')['files'],job['files'])
        def windows_limit(src,dst):
            if sys.platform=='win32' and len(str(dst))>=260:
                self.assertTrue(str(dst).startswith('\\\\?\\'),str(dst))
            return real_replace(src,dst)
        with patch.object(storage.os,'replace',side_effect=windows_limit):storage.migrate_rollbacks(self.config,self.store)
        updated=self.store.get('long');local=Path(updated['files'][0]['path'])
        self.assertGreater(len(str(local)),400);self.assertFalse(str(local).startswith('\\\\?\\'))
        self.assertEqual(io_path(local).read_bytes(),b'original long attachment');self.assertFalse(legacy.exists())
        updated['files_rolled_back']=False;storage.cleanup_rollbacks(updated,self.config,self.store)
        self.assertTrue(io_path(local).exists())
        original=self.base/'original.pdf';original.write_bytes(b'original long attachment');updated['files'][0]['path']=str(original)
        storage.cleanup_rollbacks(updated,self.config,self.store);self.assertFalse(io_path(local).exists())

    @unittest.skipUnless(sys.platform=='win32','Windows namespace conversion')
    def test_local_and_unc_namespace_round_trip_keeps_record_paths_readable(self):
        for path in (self.program/'긴제목.pdf',Path(r'\\server\share\긴제목.pdf')):
            self.assertEqual(logical_path(io_path(path)),path)
            self.assertTrue(str(io_path(path)).startswith('\\\\?\\'))

    def entry(self,complete=True):return {'sid':'fixture','tab':'ledger','row':6,'before':[''],'after':['document'],'writes':{'0':'document'},'markers':['SDR:case'],'complete':complete}

    def test_completed_browser_record_is_compacted_without_shared_or_temporary_folder(self):
        folder=self.shared/'.수발신-브라우저이력';folder.mkdir();(folder/'old.json').write_text(json.dumps(self.entry()),encoding='utf-8')
        journal=Journal(self.config);journal.migrate()
        self.assertFalse(folder.exists());self.assertFalse(journal.folder.exists());self.assertTrue(journal.database.is_relative_to(self.program))
        self.assertEqual(journal.entries()[0]['markers'],['SDR:case']);self.assertNotIn('writes',journal.entries()[0])

    def test_unfinished_browser_record_stays_local_until_verified(self):
        folder=self.shared/'.수발신-브라우저이력';folder.mkdir();(folder/'old.json').write_text(json.dumps(self.entry(False)),encoding='utf-8')
        journal=Journal(self.config);journal.migrate();self.assertFalse(folder.exists())
        path=next(journal.folder.glob('*.json'));entry=json.loads(path.read_text(encoding='utf-8'))
        self.assertFalse(entry['complete']);self.assertEqual(entry['writes'],{'0':'document'})
        entry['complete']=True;storage.save_json(path,entry);journal.archive(path,entry)
        self.assertFalse(journal.folder.exists());self.assertEqual(journal.entries()[0]['row'],6)

    def test_failed_browser_archive_never_discards_existing_shared_journal(self):
        folder=self.shared/'.수발신-브라우저이력';folder.mkdir();file=folder/'old.json';file.write_text(json.dumps(self.entry()),encoding='utf-8')
        journal=Journal(self.config)
        with patch.object(journal,'archive',side_effect=OSError('database unavailable')),self.assertRaises(OSError):journal.migrate()
        self.assertTrue(file.exists());journal.migrate();self.assertFalse(folder.exists())
