from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import additional_storage as additional
import core
import file_rollback


class AdditionalStorageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.base = self.root / '사본'
        self.base.mkdir()
        self.source = self.root / '원본'
        self.source.mkdir()
        self.pdf = self.source / '신청서.pdf'
        self.pdf.write_bytes(b'receipt file')
        self.store = core.Store(self.root / 'jobs.sqlite')
        self.addCleanup(self.store.db.close)
        self.job = {'hash': 'fixture', 'id': 'fixture', 'destinations': [str(self.pdf)],
                    'receipt_shared_files': [str(self.pdf)], 'files': [], 'receipt_done': True,
                    'number': '005'}
        self.config = {'root': str(self.source), '_program_dir': str(self.root / 'Program')}
        self.store.put(self.job)

    def save(self, folder='', scope='receipt'):
        return additional.save_additional(self.job, scope, self.base, folder, self.store)

    def test_zip_and_repeated_locations_are_copied_once(self):
        archive = self.source / '공문.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('본문.pdf', b'file')
            zipped.writestr('사진.jpg', b'image')
        second = self.root / 'second'
        second.mkdir()
        checksum = core.digest(self.pdf)
        duplicate = second / f'{self.pdf.stem}_{checksum[:12]}{self.pdf.suffix}'
        duplicate.write_bytes(self.pdf.read_bytes())
        self.job['files'] = [{'path': str(self.pdf), 'hash': checksum}]
        self.job['destinations'].extend(map(str, [archive, duplicate, second]))
        entry = self.save('업무사본')
        folder = self.base / '업무사본'
        self.assertEqual(entry['folder'], str(folder))
        self.assertEqual(entry['files'], [str(folder / p.name) for p in (self.pdf, archive)])
        self.assertEqual((folder / archive.name).read_bytes(), archive.read_bytes())
        self.assertEqual(self.save('업무사본'), entry)
        self.assertEqual(self.store.get('fixture')['additional_saves'], [entry])

    def test_collision_and_unsafe_name_leave_existing_files_untouched(self):
        (self.base / self.pdf.name).write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            self.save()
        self.assertEqual((self.base / self.pdf.name).read_bytes(), b'old')
        (self.base / 'existing').mkdir()
        with self.assertRaises(FileExistsError):
            self.save('existing')
        for name in ('../out', '.', 'NUL'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.save(name)
        self.assertNotIn('additional_save_pending', self.job)

    def test_missing_one_of_two_documents_blocks_copy(self):
        missing = self.source / '누락된 붙임.xlsx'
        self.job['destinations'].append(str(missing))
        with self.assertRaisesRegex(ValueError, '누락된 붙임'):
            self.save('사본폴더')
        self.assertEqual(list(self.base.iterdir()), [])
        self.assertNotIn('additional_save_pending', self.job)

    def test_missing_replica_uses_only_proven_same_attachment(self):
        checksum = core.digest(self.pdf)
        self.job['files'] = [{'path': str(self.pdf), 'hash': checksum}]
        missing = self.root / f'{self.pdf.stem}_{checksum[:12]}{self.pdf.suffix}'
        self.job['destinations'].insert(0, str(missing))
        self.assertEqual(self.save('A')['files'], [str(self.base / 'A' / self.pdf.name)])
        self.pdf.write_bytes(b'another document')
        with self.assertRaisesRegex(ValueError, '누락'):
            self.save('B')
        self.assertFalse((self.base / 'B').exists())

    def test_rollback_keeps_original_and_user_directory(self):
        other = self.base / '직접 작성.txt'
        other.write_bytes(b'user')
        self.save('사본폴더')
        self.save()
        self.job['created_directories'] = [{'scope': 'receipt', 'path': str(self.base / '사본폴더')}]
        file_rollback.execute(self.job, self.config, self.store, 'receipt')
        self.assertEqual(list(self.base.iterdir()), [other])
        self.assertTrue(self.pdf.is_file())
        self.assertEqual(self.job['number'], '005')
        self.assertEqual(self.job['additional_saves'], [])
        self.assertEqual(self.job['created_directories'], [])

    def test_added_file_protects_entire_folder_on_rollback(self):
        self.save('작업')
        (self.base / '작업' / '직접 추가.txt').write_bytes(b'user')
        with self.assertRaisesRegex(ValueError, '추가·수정'):
            file_rollback.plan(self.job, self.config, 'receipt')
        self.assertTrue((self.base / '작업' / self.pdf.name).exists())

    def test_independent_dispatch_and_receipt(self):
        hwp = self.source / '005. 발행.hwp'
        hwp.write_bytes(b'hwp')
        self.job['outgoing'] = {'hwp_path': str(hwp), 'hwp_created_sha256': core.digest(hwp)}
        self.save('수신')
        sent = self.save(scope='outgoing')
        self.assertEqual(sent['files'], [str(self.base / hwp.name)])
        with patch('file_rollback.hwp_available'):
            file_rollback.execute(self.job, self.config, self.store, 'outgoing')
        self.assertTrue((self.base / '수신' / self.pdf.name).is_file())
        self.assertFalse((self.base / hwp.name).exists())

    def test_foreign_replacement_with_identical_contents_is_protected(self):
        self.save()
        copy = self.base / self.pdf.name
        replacement = self.base / '사용자파일.pdf'
        replacement.write_bytes(copy.read_bytes())
        replacement.replace(copy)
        with self.assertRaisesRegex(ValueError, '교체'):
            file_rollback.execute(self.job, self.config, self.store, 'receipt')
        self.assertTrue(copy.exists())

    def test_retry_after_interruption_between_publish_and_record(self):
        publish = additional._publish
        def interrupt(stage, target):
            publish(stage, target)
            raise OSError('interrupted')
        with patch.object(additional, '_publish', side_effect=interrupt), self.assertRaises(OSError):
            self.save()
        self.job = self.store.get('fixture')
        self.assertNotIn('additional_saves', self.job)
        self.assertEqual(self.save()['files'], [str(self.base / self.pdf.name)])

    def test_unpublished_partial_file_can_be_rolled_back(self):
        with patch.object(additional.shutil, 'copyfileobj', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            self.save('작업')
        self.job = self.store.get('fixture')
        file_rollback.execute(self.job, self.config, self.store, 'receipt')
        self.assertFalse((self.base / '작업').exists())
        self.assertNotIn('additional_save_pending', self.job)

    def test_restart_retires_only_previous_outgoing_ownership(self):
        from outgoing_restart import restart
        hwp = self.source / '005.발행.hwp'
        hwp.write_bytes(b'hwp')
        self.job['outgoing'] = {'id': 'previous', 'number': '005', 'hwp_path': str(hwp)}
        incoming = self.save('수신')
        dispatched = self.save('발신', scope='outgoing')
        self.job['created_directories'] = [{'scope': 'outgoing', 'path': dispatched['folder']}]
        self.job['storage_roots'] = [{'scope': 'outgoing', 'path': dispatched['folder']}]
        with patch.object(additional.shutil, 'copyfileobj', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            self.save(scope='outgoing')
        stage = Path(self.job['additional_save_pending']['items'][0]['stage'])
        self.assertTrue(stage.is_file())
        updated = restart(self.job, self.config, self.store, expected_id='previous')
        self.assertNotEqual(updated['outgoing']['id'], 'previous')
        self.assertNotIn('additional_save_pending', updated)
        self.assertEqual(updated['additional_saves'], [incoming])
        self.assertFalse(file_rollback.has_files(updated, 'outgoing'))
        self.assertFalse(stage.exists())
        self.assertTrue(Path(dispatched['files'][0]).is_file())
        retired = updated['outgoing_attempts'][-1]
        self.assertEqual(retired['additional_saves'], [dispatched])
        self.assertEqual(retired['additional_save_pending']['scope'], 'outgoing')
        self.assertTrue(all(a['scope'] == 'receipt' for a in updated['file_artifacts']))
