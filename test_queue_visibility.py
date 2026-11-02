from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from app import Worker
from core import Store,ingest
from queue_visibility import missing_download_source


class QueueVisibilityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.downloads=self.root/"Downloads";self.downloads.mkdir()
        self.path=self.downloads/"sample.pdf";self.path.write_bytes(b"original file")
        self.job={"hash":"test","files":[{"path":str(self.path),"hash":"file"}],"state":"확인 필요","category":"","title":"sample"}

    def test_deleted_and_moved_downloads_disappear_and_can_return(self):
        self.assertFalse(missing_download_source(self.job,self.downloads))
        moved=self.root/"moved.pdf";self.path.rename(moved)
        self.assertTrue(missing_download_source(self.job,self.downloads))
        moved.rename(self.path)
        self.assertFalse(missing_download_source(self.job,self.downloads))
        self.path.unlink();self.assertTrue(missing_download_source(self.job,self.downloads))

    def test_zip_original_controls_visibility_not_extracted_cache(self):
        archive=self.downloads/"attachments.zip";archive.write_bytes(b"archive")
        cached=self.root/"cache.pdf";cached.write_bytes(b"extracted")
        self.job.update(source_archive=str(archive),files=[{"path":str(cached)}])
        self.assertFalse(missing_download_source(self.job,self.downloads))
        archive.unlink();self.assertTrue(missing_download_source(self.job,self.downloads))
        self.assertTrue(cached.exists())

    def test_group_remains_visible_while_an_attachment_exists(self):
        self.job["files"].append({"path":str(self.downloads/"already-gone.pdf")})
        self.assertFalse(missing_download_source(self.job,self.downloads))

    def test_inaccessible_folder_and_pending_registration_stay_visible(self):
        self.path.unlink()
        self.assertFalse(missing_download_source(self.job,self.root/"offline"))
        self.job["sheet_attempted"]=True
        self.assertFalse(missing_download_source(self.job,self.downloads))

    def test_manual_list_removal_preserves_file_state_and_registration(self):
        store=Store(self.root/"history.sqlite");self.addCleanup(store.db.close)
        self.job.update(state="완료",receipt_done=True,number="123",sheet_attempted=True)
        store.put(self.job)
        worker=Worker(queue.Queue(),queue.Queue(),{"downloads":str(self.downloads)},self.root/"history.sqlite");worker.store=store
        worker.hide_list([self.job["hash"]])
        result=store.get(self.job["hash"])
        self.assertTrue(result["list_hidden"])
        self.assertEqual(result["state"],"완료");self.assertEqual(result["number"],"123")
        self.assertEqual(self.path.read_bytes(),b"original file")

    def test_sync_emits_change_without_deleting_history(self):
        store=Store(self.root/"history.sqlite");self.addCleanup(store.db.close);store.put(self.job)
        events=queue.Queue();worker=Worker(queue.Queue(),events,{"downloads":str(self.downloads)},self.root/"history.sqlite");worker.store=store
        worker.sync_sources();self.assertTrue(events.empty())
        self.path.unlink();worker.sync_sources()
        self.assertEqual(events.get_nowait()[0],"job")
        self.assertEqual(store.get(self.job["hash"]),self.job)

    def test_file_add_restores_hidden_moved_item_without_new_registration(self):
        store=Store(self.root/"history.sqlite");self.addCleanup(store.db.close)
        with patch("core.extract",return_value=("제목 시험 공문",{})):
            job,_=ingest(self.path,store)
        job.update(state="완료",receipt_done=True,number="123");store.put(job)
        worker=Worker(queue.Queue(),queue.Queue(),{"downloads":str(self.downloads)},self.root/"history.sqlite");worker.store=store
        worker.hide_list([job["hash"]])
        moved=self.downloads/"renamed.pdf";self.path.rename(moved)
        worker.add(str(moved))
        restored=store.get(job["hash"])
        self.assertFalse(restored.get("list_hidden"))
        self.assertEqual(restored["state"],"완료");self.assertEqual(restored["number"],"123")
        self.assertEqual(restored["files"][0]["path"],str(moved))
        self.assertEqual(len(store.all()),1);self.assertTrue(moved.exists())
