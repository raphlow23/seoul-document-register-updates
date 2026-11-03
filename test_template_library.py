from datetime import date
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hwp_documents as docs
import template_library as library


class TemplateLibraryTests(unittest.TestCase):
    text="제목 {{제목}}\n수신 {{수신처}}\n서울센터 {{발신번호}}\n({{발신일}})\n담당 {{담당자}}"

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.source=self.base/"empty-form.hwp";self.source.write_bytes(b"fixture source bytes")
        self.root=self.base/"private"
        self.settings={**library.suggestions(self.text),"label":"새 빈 양식","family":"공문","folder":str(self.base/"output")}

    def test_suggests_whole_number_and_parenthesized_date(self):
        anchors=library.suggestions(self.text)
        self.assertEqual(anchors["number"],"서울센터 {{발신번호}}")
        self.assertEqual(anchors["date"],"({{발신일}})")
        anchors=library.suggestions("서울센터 2026-000 (26. 11. 2)")
        self.assertEqual(anchors["number"],"서울센터 2026-000")
        self.assertEqual(anchors["date"],"(26. 11. 2)")

    def test_original_filename_prefix_spacing_and_delivery_suffix(self):
        self.assertEqual(library.filename_hints("처리지침002. 26.02.04. 빈 양식(센터-발송).hwp"),{"prefix":"처리지침","spacing":True,"suffix":"(센터-발송)"})
        self.assertEqual(library.filename_hints("019.26.01.12. 빈 양식(양천구 신정동).hwp"),{"prefix":"","spacing":False,"suffix":""})
        self.assertEqual(library.number_pattern("서울센터2026-000").format(year=2027,number=12),"서울센터2027-012")

    def test_missing_repeated_overlapping_and_multiline_anchors_refused(self):
        for text,anchors in (("A B",{"number":"A","date":""}),("A A B",{"number":"A","date":"B"}),("AB",{"number":"AB","date":"B"}),("A\nB C",{"number":"A\nB","date":"C"})):
            with self.subTest(anchors=anchors),self.assertRaises(ValueError):library.validate_anchors(text,anchors)

    def test_add_private_form_preserves_source_and_existing_catalog(self):
        with patch.object(library,"inspect",return_value=self.text),patch.object(docs,"catalog",return_value=[]):
            item=library.add(self.source,self.settings,self.root)
            second=library.add(self.source,{**self.settings,"label":"추가 양식"},self.root)
        self.assertEqual(self.source.read_bytes(),(self.root/item["file"]).read_bytes())
        saved=json.loads((self.root/"catalog.json").read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in saved],[item["id"],second["id"]])
        self.assertTrue(item["blank_body"] and item["metadata_only"])
        self.assertEqual(item["old_number"],"서울센터 {{발신번호}}")

    def test_invalid_form_does_not_change_catalog(self):
        with self.assertRaises(ValueError):library.inspect(self.source)
        with patch.object(library,"inspect",return_value=self.text):
            with self.assertRaises(ValueError):library.add(self.source,{**self.settings,"date":"absent"},self.root)
        self.assertFalse(self.root.exists())

    def test_failed_catalog_commit_keeps_previous_forms(self):
        with patch.object(library,"inspect",return_value=self.text),patch.object(docs,"catalog",return_value=[]):
            library.add(self.source,self.settings,self.root)
            before={p.name:p.read_bytes() for p in self.root.iterdir()}
            with patch.object(library.os,"replace",side_effect=OSError("fixture failure")),self.assertRaises(OSError):
                library.add(self.source,{**self.settings,"label":"실패 양식"},self.root)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_copied_file_is_rechecked_before_publishing(self):
        with patch.object(library,"inspect",side_effect=[self.text,"changed file"]),patch.object(docs,"catalog",return_value=[]):
            with self.assertRaises(ValueError):library.add(self.source,self.settings,self.root)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_filename_and_printed_metadata_share_real_ledger_number(self):
        item={"blank_body":True,"family":"공문","prefix":"","spacing":" ","suffix":""}
        out={"number":"12","title":"안내 공문","body":""}
        values=docs.native_values(item,out,date(2026,11,2))
        self.assertEqual(values["number_date"],"서울센터 2026-012")
        self.assertEqual(values["display_date"],"(26. 11. 2)")
        self.assertEqual(docs.filename(item,out,date(2026,11,2)),"012. 26.11.02. 안내 공문.hwp")
        with self.assertRaises(ValueError):docs.native_values(item,{**out,"file_number":"13"})

    def test_blank_form_only_replaces_specified_fields(self):
        item={"file":"empty.hwp","title_fragments":[],"receiver":"","old_sender":"","old_number":"서울센터 {{발신번호}}","old_date":"({{발신일}})","metadata_only":True}
        params=SimpleNamespace(HSet=object());calls=[]
        actions=SimpleNamespace(Run=lambda *a:None,GetDefault=lambda *a:None,Execute=lambda *a:calls.append((params.FindString,params.ReplaceString)))
        hwp=SimpleNamespace(HAction=actions,HParameterSet=SimpleNamespace(HFindReplace=params),FindDir=lambda *a:0)
        with patch("core.extract",return_value=self.text):docs.replace_native_fields(hwp,item,{"title":"직접 입력 제목","receiver":"기관","sender":"담당자","number_date":"서울센터 2026-012","display_date":"(26. 11. 2)"})
        self.assertCountEqual(calls,[("서울센터 {{발신번호}}","서울센터 2026-012"),("({{발신일}})","(26. 11. 2)")])

    def test_native_blank_body_can_be_saved_without_generating_body(self):
        item={"id":"blank","file":"empty.hwp","blank_body":True,"metadata_only":True,"title_fragments":[],"receiver":"","old_sender":"","old_date":"{{발신일}}","old_number":"{{발신번호}}","family":"공문","prefix":"","spacing":" ","suffix":""}
        out={"number":"12","title":"공문 제목","receiver":"수신 기관","sender":"담당자","ledger_type":"공문","body":""}
        output=self.base/docs.filename(item,out)
        values=docs.native_values(item,out)
        class Hancom:
            def Open(self,*a):return True
            def SaveAs(self,path,*a):Path(path).write_bytes(b"saved blank HWP");return True
            def Clear(self,*a):pass
            def Quit(self):pass
        with patch.object(docs,"template_body",return_value=""),patch.object(docs,"replace_native_fields"),patch("core.extract",return_value=values["number_date"]+values["display_date"]),patch("hwp_fidelity.verify"):
            docs.export_hwp(item,out,output,factory=Hancom)
        self.assertEqual(output.read_bytes(),b"saved blank HWP")
        self.assertFalse(out["hwp_body_paste_needed"])

    def test_registered_hwp_link_survives_stale_editor_save(self):
        from outgoing import merge_edit
        old={"complete":True,"id":"existing","number":"12","hwp_saved_number":"12","hwp_path":str(self.base/"edited.hwp"),'hwp_created_sha256':'original-hash'}
        job={"state":"수신 완료·발신 등록 대기","outgoing":old}
        merge_edit(job,{"enabled":True,"stamp":{},"title":"저장된 제목"})
        self.assertEqual(job["outgoing"]["hwp_path"],old["hwp_path"])
        self.assertEqual(job["outgoing"]["hwp_saved_number"],"12")
        self.assertEqual(job['outgoing']['hwp_created_sha256'],'original-hash')


if __name__=="__main__":unittest.main()
