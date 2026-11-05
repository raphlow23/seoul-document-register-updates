from pathlib import Path
from copy import deepcopy
from unittest.mock import Mock
import unittest
import test_file_rollback as fixture
from receipt_attachments import append,load_sources,discard_sources
import file_rollback
import core


class AttachmentTests(unittest.TestCase):
    setUp=fixture.FileRollbackTests.setUp
    save=fixture.FileRollbackTests.save
    def extra(self):
        source=self.base/'extra.pdf';source.write_bytes(b'extra attachment')
        job={'hash':core.digest(source),'id':'new-fixture','title':'첨부','state':'확인 필요','files':[{'path':str(source),'hash':core.digest(source)}]}
        self.store.put(job);return job

    def test_append_to_registered_case_rolls_back_files_and_keeps_sheet_and_title(self):
        folder=self.save();new=self.extra();identity=deepcopy({k:self.job[k] for k in ('id','title','number','row','sheet_attempted','receipt_done')})
        append(self.job,[new],self.config,self.store)
        self.assertFalse(folder.exists());self.assertEqual(len(self.job['files']),2)
        self.assertEqual({k:self.job[k] for k in identity},identity)
        self.assertEqual(len(self.job['receipt_marker_files']),1)
        self.assertEqual(self.store.get(new['hash'])['parent'],self.job['hash'])
        forbidden=Mock(side_effect=AssertionError('No new sheet registration'))
        core.process(self.job,self.config,self.store,sheets_factory=forbidden)
        self.assertEqual(len(self.job['destinations']),2);forbidden.assert_not_called()

    def test_append_after_explicit_back_does_not_create_another_backup(self):
        self.save();file_rollback.execute(self.job,self.config,self.store,'receipt')
        append(self.job,[self.extra()],self.config,self.store)
        self.assertEqual(len(self.job['rollback_history']),1)

    def test_invalid_new_source_does_not_remove_existing_case(self):
        folder=self.save();extra=self.extra();Path(extra['files'][0]['path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):append(self.job,[extra],self.config,self.store)
        self.assertTrue(folder.exists());self.assertEqual(len(self.job['files']),1)

    def test_uncertain_registration_keeps_original_marker_set(self):
        self.save();self.job.pop('receipt_done');self.job['state']='저장 완료·등록 대기'
        old=[f['hash'] for f in self.job['files']]
        append(self.job,[self.extra()],self.config,self.store)
        self.assertEqual([f['hash'] for f in self.job['receipt_marker_files']],old)
        self.assertTrue(self.job['sheet_attempted'])

    def test_zip_can_add_missing_files_while_including_the_original_registered_attachment(self):
        import zipfile
        folder=self.save();path=self.base/'new attachments.zip'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('original.pdf',self.source.read_bytes());archive.writestr('extra.txt',b'extra file')
        sources=load_sources([path],self.job,self.store)
        append(self.job,sources,self.config,self.store);discard_sources(sources,self.store)
        self.assertFalse(folder.exists());self.assertEqual(len(self.job['files']),1);self.assertEqual(len(self.job['archive_files']),1);self.assertEqual(self.job['number'],'581')
        self.assertTrue(all('title' in entry and 'state' in entry for entry in self.store.all()))
        self.assertTrue(all(Path(file['path']).is_file() for file in self.job['files']))
        forbidden=Mock(side_effect=AssertionError('No duplicate receipt registration'))
        core.process(self.job,self.config,self.store,sheets_factory=forbidden)
        saved=next(Path(p) for p in self.job['destinations'] if Path(p).suffix=='.zip')
        self.assertEqual(saved.read_bytes(),path.read_bytes());forbidden.assert_not_called()

    def test_zip_cannot_take_another_registered_cases_attachment(self):
        import zipfile
        folder=self.save();extra=self.extra();extra.update(receipt_done=True,state='완료');self.store.put(extra)
        path=self.base/'foreign.zip'
        with zipfile.ZipFile(path,'w') as archive:archive.writestr('other.pdf',Path(extra['files'][0]['path']).read_bytes())
        sources=load_sources([path],self.job,self.store)
        with self.assertRaises(ValueError):append(self.job,sources,self.config,self.store)
        discard_sources(sources,self.store);self.assertTrue(folder.exists())
