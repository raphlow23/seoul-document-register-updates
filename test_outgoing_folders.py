from copy import deepcopy
from pathlib import Path
import json
import queue
import tempfile
import unittest
from unittest.mock import patch

import folder_naming as naming
from outgoing_folders import prepare,validate
from outgoing import merge_edit
from core import Store


class FolderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.base=self.root/"시행문";self.base.mkdir()
        self.store=Store(self.root/"history.sqlite");self.addCleanup(self.store.db.close)

    def job(self,template="{number:03d}. {title}",identity="out1"):
        return {"hash":identity,"id":"receipt1","state":"완료","title":"구글 시트 수신 제목","receipt_done":True,"number":"581","outgoing":{"id":identity,"title":"구글 시트 발신 제목","number":"438","date":"2026-11-02","save_folder":str(self.base),"folder_plan":{"base":str(self.base),"template":template,"folder_title":"사업명과 주소","date":"2026-11-02","agency":"서울시청 공문","children":["incoming","reply"],"save_in":"reply"}}}

    def test_infer_number_prefix_brackets_and_spaced_dates(self):
        cases={"처리지침009. 26. 11. 2. 이전 제목":"처리지침010. 26. 11. 3. 새 제목","[005] 26.11.02. 이전 제목":"[006] 26.11.03. 새 제목","(005) 이전 제목":"(006) 새 제목"}
        for old,expected in cases.items():
            template=naming.infer_template(old)
            self.assertIsNotNone(template)
            self.assertEqual(naming.folder_name(template,"새 제목","2026-11-03",naming.sequence_from_names([old],template)),expected)

    def test_institution_suffix_is_kept_once_but_addresses_are_document_content(self):
        template=naming.infer_template("처리지침002. 26.02.04. 이전 안내(센터-발송)")
        self.assertEqual(naming.folder_name(template,"새 안내","2026-11-03",3),"처리지침003. 26.11.03. 새 안내(센터-발송)")
        self.assertEqual(naming.folder_name(template,"새 안내(센터-발송)","2026-11-03",3),"처리지침003. 26.11.03. 새 안내(센터-발송)")
        template=naming.infer_template("001. 사업 협의(양천구 신정동)")
        self.assertEqual(naming.folder_name(template,"다른 사업(서대문구)","2026-11-03",2),"002. 다른 사업(서대문구)")

    def test_empty_changed_destination_keeps_previous_style_but_restarts_number(self):
        (self.base/"057. 26.11.02. 지난 공문").mkdir()
        config={};record=naming.describe_directory(self.base)
        template=naming.preferred_template(record,config,"outgoing:6")
        naming.remember_profile(config,"outgoing:6",self.base,template,"서울시청 공문",["incoming","reply"],"reply")
        changed=self.root/"새 저장 위치";changed.mkdir()
        record=naming.describe_directory(changed)
        inherited=naming.preferred_template(record,config,"outgoing:6")
        self.assertEqual(inherited,template)
        self.assertEqual(naming.folder_name(inherited,"새 공문","2026-11-03",naming.sequence_from_names(record["names"],inherited)),"001. 26.11.03. 새 공문")
        self.assertEqual(config["folder_children"]["서울시청 공문"],["incoming","reply"])

    def test_date_only_style_stays_date_only_in_empty_location(self):
        config={};naming.remember_profile(config,"incoming:0.시청공문",self.base,"{yy:02d}.{month:02d}.{day:02d}. {title}","서울시청 공문",[])
        template=naming.preferred_template(naming.describe_directory(self.base),config,"incoming:0.시청공문")
        job=self.job(template);prepare(job,{"root":str(self.root)},self.store)
        self.assertEqual(job["outgoing"]["folder_plan"]["folder_name"],"26.11.02. 사업명과 주소")
        self.assertIsNone(job["outgoing"]["folder_plan"]["folder_number"])

    def test_nonempty_new_location_uses_its_own_existing_style(self):
        config={};naming.remember_profile(config,"outgoing:6",self.root/"old","{number:03d}. {title}","서울시청 공문",[])
        (self.base/"2026.11.02_기존 공문").mkdir()
        template=naming.preferred_template(naming.describe_directory(self.base),config,"outgoing:6")
        self.assertNotIn("number",template)
        self.assertEqual(naming.folder_name(template,"새 공문","2026-11-03"),"2026.11.03_새 공문")

    def test_outgoing_case_number_is_independent_and_resume_reuses_folder(self):
        (self.base/"004. 이전 공문").mkdir()
        job=self.job();path=prepare(job,{"root":str(self.root)},self.store)
        self.assertEqual(path,self.base/"005. 사업명과 주소"/"2.시청회신")
        self.assertTrue((path.parent/"1.시청공문").is_dir())
        self.assertEqual(job["number"],"581");self.assertEqual(job["outgoing"]["number"],"438")
        self.assertEqual(job["outgoing"]["title"],"구글 시트 발신 제목")
        self.assertEqual(prepare(job,{"root":str(self.root)},self.store),path)
        self.assertEqual(len([p for p in self.base.iterdir() if p.is_dir()]),2)
        other=self.job(identity="out2");prepare(other,{"root":str(self.root)},self.store)
        self.assertEqual(other["outgoing"]["folder_plan"]["folder_number"],6)

    def test_existing_unrelated_folder_is_not_modified(self):
        job=self.job("{title}");foreign=self.base/"사업명과 주소";foreign.mkdir()
        (foreign/"original.txt").write_text("keep",encoding="utf-8")
        with self.assertRaises(ValueError):prepare(job,{"root":str(self.root)},self.store)
        self.assertEqual([p.name for p in foreign.iterdir()],["original.txt"])

    def test_created_plan_cannot_be_renamed_by_stale_editor(self):
        job=self.job();prepare(job,{"root":str(self.root)},self.store)
        changed=deepcopy(job["outgoing"]);changed["folder_plan"]["folder_title"]="다른 제목"
        with self.assertRaises(ValueError):merge_edit(job,changed)

    def test_receipt_folder_title_is_independent_of_sheet_title(self):
        base=self.root/"0.시청공문";base.mkdir()
        source=self.root/"fixture.pdf";source.write_bytes(b"fixture")
        from core import digest
        job={"id":"receipt fixture","hash":digest(source),"title":"시트에 등록할 제목","date":"2026-11-02","files":[{"hash":digest(source),"path":str(source)}],"routing":{"agency":"서울시청 공문","folder_title":"사업명 주소를 덧붙인 폴더 제목","children":["incoming","reply"],"targets":[{"relative":"0.시청공문","template":"{number:03d}. {title}"}]}}
        naming.copy_to_subfolders(job,self.root)
        self.assertTrue((base/"001. 사업명 주소를 덧붙인 폴더 제목"/"1.시청공문"/"fixture.pdf").is_file())
        self.assertEqual(job["title"],"시트에 등록할 제목")

    def test_worker_exports_hwp_into_prepared_internal_folder(self):
        from app import Worker
        job=self.job();out=job["outgoing"]
        out.update(template_id="2",ledger_type="공문",receiver="기관",sender="담당자",body="",stamp={})
        self.store.put(job);commands,events=queue.Queue(),queue.Queue()
        worker=Worker(commands,events,{"root":str(self.root)},self.root/"history.sqlite")
        exported=[]
        def export(item,out,path):
            exported.append(Path(path));Path(path).write_bytes(b"HWP fixture")
        with patch("hwp_documents.export_hwp",side_effect=export):
            worker.start();commands.put(("draft_export",{"hash":job["hash"],"outgoing":deepcopy(out),"path":str(self.base/"unused.hwp"),"native_hwp":True,"copy_only":False}));commands.put(("quit",None));worker.join(timeout=8)
        errors=[]
        while not events.empty():
            kind,payload=events.get_nowait()
            if kind=="error":errors.append(payload)
        self.assertFalse(errors,errors);self.assertEqual(len(exported),1)
        self.assertEqual(exported[0].parent,self.base/"001. 사업명과 주소"/"2.시청회신")
        self.assertTrue(exported[0].name.startswith("438."))
        self.assertEqual(self.store.get(job["hash"])["outgoing"]["hwp_path"],str(exported[0]))


if __name__=="__main__":unittest.main()
