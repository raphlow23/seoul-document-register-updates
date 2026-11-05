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
    def test_zip_without_office_documents_is_skipped_without_an_error_popup(self):
        from app import Worker
        import queue
        worker=Worker(queue.Queue(),queue.Queue(),{'downloads':str(self.root)},self.root/'unused.sqlite');worker.store=self.store
        path=self.archive('사진만.zip',{'사진.jpg':b'image','설명.txt':b'notes','data.bin':b'other'})
        worker.add(path)
        self.assertIsNone(self.store.get(core.digest(path)));self.assertNotIn('error',[kind for kind,_ in worker.events.queue])

    def test_zip_with_office_document_preserves_every_other_file_inside_original_zip(self):
        path=self.archive('공문과 기타파일.zip',{'공문.hwpx':hwpx('시험 제목'),'사진.jpg':b'private image fixture','자료.bin':b'other file fixture','설명.txt':b'notes'})
        original=path.read_bytes();job,_=core.ingest(path,self.store)
        self.assertEqual({Path(f['path']).suffix for f in job['files']},{'.hwpx'})
        target=self.root/'saved';target.mkdir();copied=core.copy_files(job,target,subfolders=['.'])
        saved=next(Path(p) for p in copied if Path(p).suffix=='.zip')
        self.assertEqual(saved.read_bytes(),original);self.assertEqual(path.read_bytes(),original)
        with zipfile.ZipFile(saved) as archive:self.assertEqual(archive.read('자료.bin'),b'other file fixture');self.assertIn('사진.jpg',archive.namelist())

    def test_supported_office_and_drawing_extensions_qualify_a_zip(self):
        for extension in ('.hwpx','.pdf','.hwp','.html','.dwg','.dxf','.docx','.xlsx','.pptx'):
            with self.subTest(extension=extension):
                content=hwpx('시험 '+extension) if extension=='.hwpx' else ('fixture '+extension).encode()
                path=self.archive('bundle'+extension+'.zip',{'document'+extension:content,'other.bin':b'other'})
                job,new=core.ingest(path,self.store);self.assertTrue(new);self.assertTrue(job['archive_files'])
    def test_existing_zip_is_skipped_and_new_zip_is_visible_as_a_bundle(self):
        from app import Worker
        import queue,time
        path=self.archive('기존 첨부.ZIP');pdf=self.root/'baseline.pdf';pdf.write_bytes(b'old file')
        commands,events=queue.Queue(),queue.Queue()
        worker=Worker(commands,events,{'downloads':str(self.root)},self.root/'history.sqlite');worker.start()
        try:
            commands.put(('watch',True))
            while events.get(timeout=10)[0]!='idle':pass
            self.assertIn(str(path),worker.seen);self.assertIn(str(pdf),worker.seen)
        finally:commands.put(('quit',None));worker.join(timeout=10)
        local=Worker(queue.Queue(),queue.Queue(),{'downloads':str(self.root)},self.root/'unused.sqlite');local.store=self.store
        local.seen=local.files();old=path
        path=self.archive("새 첨부.ZIP",{"수신공문.hwpx":hwpx("새 제목"),"붙임.xlsx":b"new table"})
        local.pending[str(path)]=(local.files()[str(path)],time.monotonic()-7);local.scan()
        job=self.store.get(core.digest(path));self.assertEqual(job['source_archive'],str(path));self.assertEqual(len(job['files']),2);self.assertIsNone(self.store.get(core.digest(old)))
        from workflow_ui import WorkflowShell
        from types import SimpleNamespace
        from unittest.mock import Mock
        shell=WorkflowShell.__new__(WorkflowShell);shell.row=Mock();tree=Mock();tree.get_children.return_value=()
        shell.app=SimpleNamespace(tree=tree,jobs={},config={'downloads':str(self.root)});shell.history_tree=Mock();shell.outgoing_list_tree=Mock()
        shell.update_job(job);self.assertTrue(shell.row.call_args_list[0].args[2]);self.assertIn('[ZIP] 새 첨부.ZIP',shell.row.call_args_list[0].args[3][2])

    def test_upgrade_from_old_zip_catchup_policy_does_not_replay_existing_files(self):
        from app import Worker
        import queue,time,json
        path=self.archive();commands,events=queue.Queue(),queue.Queue()
        (self.root/'watch-resume.json').write_text(json.dumps({'downloads':str(self.root),'time':time.time(),'seen':{}}),encoding='utf-8')
        worker=Worker(commands,events,{'downloads':str(self.root)},self.root/'history.sqlite');worker.start()
        try:
            commands.put(('watch',True))
            while events.get(timeout=10)[0]!='idle':pass
            self.assertIn(str(path),worker.seen);self.assertIsNone(self.store.get(core.digest(path)))
        finally:commands.put(('quit',None));worker.join(timeout=10)
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

    def test_documents_html_drawings_retained_and_images_excluded_from_bundle(self):
        path=self.archive(members={'공문.hwpx':hwpx('시험 공문'),'설명.html':b'html','도면.dwg':b'dwg','도면.dxf':b'dxf','사진.jpg':b'jpg','로고.png':b'png'})
        original=path.read_bytes();job,_=core.ingest(path,self.store)
        self.assertEqual({Path(f['path']).name for f in job['files']},{'공문.hwpx','설명.html','도면.dwg','도면.dxf'})
        self.assertEqual(path.read_bytes(),original)

    def test_nested_zip_and_korean_file_names(self):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,"w") as z:z.writestr("공문/안내.hwpx",hwpx("안내 제목"))
        path=self.archive(members={"추가첨부.zip":inner.getvalue(),"붙임/설명.txt":"설명".encode()})
        job,_=core.ingest(path,self.store)
        self.assertEqual({Path(f["path"]).name for f in job["files"]},{"안내.hwpx"})
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

    def test_different_zip_is_reviewed_as_a_new_case_without_inheriting_an_edited_title(self):
        first,_=core.ingest(self.archive(),self.store)
        first["title"]="사용자가 수정한 제목";self.store.put(first)
        other=self.archive("다른 압축.zip")
        # Change the archive wrapper without changing any attachment bytes.
        with zipfile.ZipFile(other,"a") as z:z.comment=b"different download"
        again,new=core.ingest(other,self.store)
        self.assertTrue(new);self.assertNotEqual(again["id"],first["id"])
        self.assertNotEqual(again["title"],first["title"])
        self.assertEqual(again["number"],"")
        self.assertEqual(self.store.get(first["hash"])["title"],"사용자가 수정한 제목")
        self.assertEqual(len(list((self.root/"archive-cache").iterdir())),2)
        again,_=core.ingest(other,self.store)
        self.assertEqual(again["hash"],core.digest(other))

    def test_shared_attachment_does_not_make_a_new_zip_an_already_processed_case(self):
        path=self.archive(members={"공문.hwpx":hwpx("기존 제목")})
        old,_=core.ingest(path,self.store);old["files_started"]=True;self.store.put(old)
        new=self.archive("새 묶음.zip",{"공문.hwpx":hwpx("기존 제목"),"새문서.hwpx":hwpx("새 제목")})
        new_job,created=core.ingest(new,self.store)
        self.assertTrue(created);self.assertEqual(new_job["number"],"");self.assertNotIn("receipt_done",new_job)
        self.assertTrue(self.store.get(old["hash"])["files_started"])
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
        self.assertEqual(len(copied),3);self.assertEqual(len(set(copied)),3)
        self.assertEqual({core.digest(p) for p in copied},{f["hash"] for f in [*job["files"],*job['archive_files']]})
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
        self.assertNotIn("error",[kind for kind,_ in list(worker.events.queue)])


if __name__=="__main__":unittest.main()
