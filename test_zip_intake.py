import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import core
import zip_intake as intake


def hwpx(title):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w") as z:
        z.writestr(zipfile.ZipInfo("Contents/section0.xml",date_time=(1980,1,1,0,0,0)),f'<section xmlns:h="urn:test"><h:p><h:t>서울특별시장</h:t></h:p><h:p><h:t>제목 {title}</h:t></h:p><h:p><h:t>실제 공문 본문을 확인합니다.</h:t></h:p></section>')
    return buffer.getvalue()


class ZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.store=core.Store(self.root/"history.sqlite");self.addCleanup(self.store.db.close)

    def archive(self,name="첨부.zip",members=None):
        path=self.root/name
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
            for member,data in (members or {"수신공문.hwpx":hwpx("기본 수신 제목"),"자료/붙임.hwpx":hwpx("붙임 제목"),"자료/목록.xlsx":b"preserved attachment"}).items():z.writestr(member,data)
        return path

    def test_bundle_real_documents_and_other_attachments_persist_after_restart(self):
        path=self.archive()
        job,new=core.ingest(path,self.store)
        self.assertTrue(new);self.assertEqual(len(job["files"]),3)
        self.assertEqual(job["state"],"확인 필요");self.assertFalse(job["ready"])
        self.assertTrue(job["source_archive"].endswith("첨부.zip"))
        for f in job["files"]:
            self.assertTrue(Path(f["path"]).is_file())
            self.assertTrue(Path(f["path"]).resolve().is_relative_to(self.root/"archive-cache"))
            self.assertEqual(core.digest(f["path"]),f["hash"])
        again,new=core.ingest(path,self.store)
        self.assertFalse(new);self.assertEqual(again["id"],job["id"])

    def test_nested_zip_and_korean_file_names(self):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,"w") as z:z.writestr("공문/안내.hwpx",hwpx("안내 제목"))
        path=self.archive(members={"추가첨부.zip":inner.getvalue(),"붙임/설명.txt":"설명".encode()})
        job,_=core.ingest(path,self.store)
        self.assertEqual({Path(f["path"]).name for f in job["files"]},{"안내.hwpx","설명.txt"})
        self.assertIn("안내 제목",job["text"])

    def test_import_does_not_rename_extraction_directory_on_windows(self):
        path=self.archive();original=path.read_bytes()
        with patch.object(Path,'rename',side_effect=PermissionError(5,'Access denied')) as rename:
            job,new=core.ingest(path,self.store)
        rename.assert_not_called();self.assertTrue(new)
        self.assertTrue(all(Path(f['path']).is_file() for f in job['files']))
        self.assertEqual(path.read_bytes(),original)

    def test_legacy_cp949_zip_names(self):
        raw="한글.hwpx".encode("cp949");placeholder=b"a"*len(raw)
        path=self.archive(members={placeholder.decode():hwpx("한글 파일 제목")})
        path.write_bytes(path.read_bytes().replace(placeholder,raw))
        job,_=core.ingest(path,self.store)
        self.assertEqual(Path(job["files"][0]["path"]).name,"한글.hwpx")

    def test_different_zip_same_contents_reuses_group_and_edited_title(self):
        first,_=core.ingest(self.archive(),self.store)
        first["title"]="사용자가 수정한 제목";self.store.put(first)
        other=self.archive("다른 압축.zip")
        # Change the archive wrapper without changing any attachment bytes.
        with zipfile.ZipFile(other,"a") as z:z.comment=b"different download"
        again,new=core.ingest(other,self.store)
        self.assertFalse(new);self.assertEqual(again["id"],first["id"])
        self.assertEqual(again["title"],first["title"])
        self.assertEqual(len(list((self.root/"archive-cache").iterdir())),1)
        again,_=core.ingest(other,self.store)
        self.assertEqual(again["hash"],first["hash"])

    def test_partial_already_processed_content_is_not_silently_re_registered(self):
        path=self.archive(members={"공문.hwpx":hwpx("기존 제목")})
        old,_=core.ingest(path,self.store);old["files_started"]=True;self.store.put(old)
        new=self.archive("새 묶음.zip",{"공문.hwpx":hwpx("기존 제목"),"새문서.hwpx":hwpx("새 제목")})
        with self.assertRaisesRegex(ValueError,"기존 묶음"):core.ingest(new,self.store)
        self.assertEqual(self.store.get(old["hash"])["id"],old["id"])

    def test_cached_text_identifies_correct_title_source(self):
        job,_=core.ingest(self.archive(),self.store)
        for f in job["files"]:
            cached=self.store.get(f["hash"])
            if cached.get("text"):self.assertEqual(cached["text_file_hash"],f["hash"])

    def test_reject_unsafe_paths_links_and_duplicate_names_and_cleanup(self):
        for name in ("../outside.hwpx","/outside.hwpx","C:/outside.hwpx","data:stream","NUL.hwpx","trailing. /x.hwpx"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):core.ingest(self.archive(members={name:b"bad"}),self.store)
        link=zipfile.ZipInfo("link.hwpx");link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        path=self.root/"link.zip"
        with zipfile.ZipFile(path,"w") as z:z.writestr(link,b"../../other")
        with self.assertRaisesRegex(ValueError,"링크"):core.ingest(path,self.store)
        with self.assertRaises(ValueError):core.ingest(self.archive(members={"x.hwpx":b"a","X.hwpx":b"b"}),self.store)
        self.assertFalse((self.root/"outside.hwpx").exists())
        self.assertFalse(list((self.root/"archive-cache").iterdir()))

    def test_size_count_depth_limits(self):
        path=self.archive()
        for key,value in (("MAX_MEMBER",3),("MAX_TOTAL",3),("MAX_ENTRIES",1)):
            with patch.object(intake,key,value):
                with self.assertRaises(ValueError):core.ingest(path,self.store)
        inner=path.read_bytes()
        for _ in range(5):
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,"w") as z:z.writestr("inner.zip",inner)
            inner=buf.getvalue()
        path.write_bytes(inner)
        with self.assertRaisesRegex(ValueError,"3단계"):core.ingest(path,self.store)

    def test_same_basename_different_contents_both_copy_and_source_zip_unchanged(self):
        path=self.archive(members={"A/공문.hwpx":hwpx("첫 제목"),"B/공문.hwpx":hwpx("둘째 제목")})
        original=path.read_bytes();job,_=core.ingest(path,self.store)
        target=self.root/"shared";target.mkdir();(target/"case").mkdir()
        copied=core.copy_files(job,target,subfolders=["case"])
        self.assertEqual(len(copied),2);self.assertEqual(len(set(copied)),2)
        self.assertEqual({core.digest(p) for p in copied},{f["hash"] for f in job["files"]})
        self.assertEqual(path.read_bytes(),original)

    def test_corrupt_empty_zip_and_worker_does_not_stop_watch(self):
        path=self.root/"broken.zip";path.write_bytes(b"unfinished")
        with self.assertRaises(ValueError):core.ingest(path,self.store)
        with zipfile.ZipFile(path,"w"):pass
        with self.assertRaisesRegex(ValueError,"첨부파일"):core.ingest(path,self.store)
        from app import Worker
        import queue
        worker=Worker(queue.Queue(),queue.Queue(),{},self.root/"unused.sqlite")
        worker.store=self.store;worker.watch=True
        worker.add(path)
        self.assertTrue(worker.watch)
        self.assertIn("error",[kind for kind,_ in list(worker.events.queue)])


if __name__=="__main__":unittest.main()
