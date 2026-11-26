from copy import deepcopy
from pathlib import Path
import json,os,queue,tempfile,time,unittest,zipfile
from types import SimpleNamespace
from unittest.mock import Mock,patch
import core
from app import Worker,App
from queue_visibility import resume_downloads
from test_zip_intake import hwpx


class ReimportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.path=self.base/'공문.hwpx';self.path.write_bytes(hwpx('원본 제목'))
        self.worker=Worker(queue.Queue(),queue.Queue(),{'downloads':str(self.base),'recipient':'기본 담당자'},self.base/'history.sqlite');self.worker.store=self.store

    def completed(self,path=None):
        job,_=core.ingest(path or self.path,self.store);core.receipt_markers(job)
        job.update(state='완료',receipt_done=True,sheet_attempted=True,number='581',row=600,routing={'old':'keep'},outgoing={'id':'old-out','number':'444','sheet_attempted':True})
        self.store.put(job);self.worker.hide_list([job['hash']]);return job

    def test_fresh_intake_keeps_old_record_and_uses_distinct_sheet_markers(self):
        old=self.completed();fresh,new=core.ingest(self.path,self.store)
        self.assertTrue(new);self.assertNotEqual(fresh['id'],old['id']);self.assertEqual(fresh['number'],'')
        for key in ('receipt_done','sheet_attempted','row','routing','outgoing','files_started','title_source'):self.assertNotIn(key,fresh)
        preserved=self.store.get(fresh['reimported_from'])
        for key in ('id','number','row','routing','outgoing'):self.assertEqual(preserved[key],old[key])
        self.assertNotEqual(core.receipt_markers(old),core.receipt_markers(fresh))
        again,new=core.ingest(self.path,self.store);self.assertFalse(new);self.assertEqual(again['id'],fresh['id'])

    def test_hiding_alone_does_not_rediscover_an_unchanged_existing_file(self):
        job=self.completed();self.worker.seen=self.worker.files();self.worker.events=queue.Queue();self.worker.scan()
        self.assertFalse(self.worker.pending);self.assertTrue(self.store.get(job['hash'])['list_hidden'])

    def test_closed_downloads_survive_startup_and_are_added_only_once(self):
        self.worker.restore_watch_state()
        self.worker.save_watch_state(True)
        state = json.loads(self.worker.watch_state.read_text(encoding='utf-8'))
        state['time'] -= 7*86400
        self.worker.watch_state.write_text(json.dumps(state),encoding='utf-8')
        bundle=self.base/'메일 붙임.zip'
        with zipfile.ZipFile(bundle,'w') as z:z.writestr('접수.hwpx',hwpx('새 공문'))
        sheet=self.base/'자료.xlsx'
        sheet.write_bytes(b'table fixture')
        (self.base/'사진.jpg').write_bytes(b'image fixture')
        (self.base/'미완료.crdownload').write_bytes(b'partial')
        worker=Worker(queue.Queue(),queue.Queue(),self.worker.config,self.worker.dbpath)
        worker.store=self.store
        worker.restore_watch_state()
        worker.save_watch_state(True)  # automatic update check starts before scanning
        worker.restore_watch_state()
        worker.scan()
        self.assertEqual(set(worker.pending),{str(bundle),str(sheet)})
        worker.pending={p:(sig,time.monotonic()-7) for p,(sig,_) in worker.pending.items()}
        worker.scan()
        self.assertIsNotNone(self.store.intake(core.digest(bundle)))
        self.assertIsNotNone(self.store.intake(core.digest(sheet)))
        self.assertIsNone(self.store.intake(core.digest(self.path)))
        worker.restore_watch_state()
        worker.scan()
        self.assertEqual(worker.pending,{})

    def test_new_download_directory_starts_with_its_existing_files(self):
        self.worker.restore_watch_state()
        self.worker.save_watch_state(True)
        other=self.base/'other'
        other.mkdir()
        path=other/'이미 있던 파일.xlsx'
        path.write_bytes(b'old')
        self.worker.config['downloads']=str(other)
        self.worker.restore_watch_state()
        self.assertEqual(self.worker.seen,self.worker.files())

    def test_observed_delete_then_same_signature_download_is_detected(self):
        old=self.completed();signature=self.worker.files()[str(self.path)];data=self.path.read_bytes()
        self.worker.seen=self.worker.files();self.path.unlink();self.worker.scan();self.assertNotIn(str(self.path),self.worker.seen)
        self.path.write_bytes(data);os.utime(self.path,ns=(signature[1],signature[1]))
        self.worker.pending[str(self.path)]=(signature,time.monotonic()-7);self.worker.scan()
        fresh=self.store.intake(old['hash']);self.assertNotEqual(fresh['id'],old['id']);self.assertEqual(fresh['number'],'')

    def test_same_zip_reimport_uses_new_cache_and_preserves_prior_alias_owners(self):
        path=self.base/'메일함께다운로드.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('공문.hwpx',hwpx('원본 제목'));z.writestr('붙임.xlsx',b'fixture table')
        old=self.completed(path);fresh,new=core.ingest(path,self.store)
        self.assertTrue(new);self.assertEqual(len(fresh['files']),2);self.assertNotEqual(old['files'][0]['path'],fresh['files'][0]['path'])
        self.assertTrue(all(Path(f['path']).is_file() for f in old['files']+fresh['files']))
        for file in old['files']:self.assertEqual(self.store.get(file['hash'])['parent'],fresh['reimported_from'])
        self.assertNotEqual(core.receipt_markers(old),core.receipt_markers(fresh))

    def test_failed_zip_reimport_leaves_previous_record_untouched(self):
        path=self.base/'archive.zip';path.write_bytes(b'not a zip');hash_=core.digest(path)
        old={'hash':hash_,'id':'original','state':'완료','number':'581','list_hidden':True,'files':[]};self.store.put(old)
        with self.assertRaises(ValueError):core.ingest(path,self.store)
        self.assertEqual(self.store.get(hash_),old);self.assertEqual(len(self.store.all()),1)

    def test_startup_checks_watch_by_default_even_after_disabled_resume(self):
        (self.base/'watch-resume.json').write_text(json.dumps({'watch':False}))
        app=App.__new__(App);app.data=self.base;app.commands=queue.Queue();app.watching=Mock();app.toggle_watch=Mock()
        app.start_watch();app.watching.set.assert_called_once_with(True);app.toggle_watch.assert_not_called()
        self.assertEqual(app.commands.get_nowait(),('watch',True));self.assertTrue(app.commands.empty())
        inventory={'old.zip':(100,1),'old.pdf':(100,1)}
        self.assertEqual(resume_downloads({'watch':False,'seen':{}},inventory),inventory)
