from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from lxml import etree

import hwp_documents as docs
from document_draft import HP


class HwpTests(unittest.TestCase):
    def setUp(self):
        legacy=patch.object(docs,"template_root",return_value=Path(__file__).resolve().parent/"hwp-templates")
        legacy.start();self.addCleanup(legacy.stop);docs.template_body.cache_clear();self.addCleanup(docs.template_body.cache_clear)
        original=json.loads((docs.template_root()/'catalog.json').read_text(encoding='utf-8'))
        legacy_catalog=patch.object(docs,'catalog',return_value=original);legacy_catalog.start();self.addCleanup(legacy_catalog.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)

    def fixture(self,item):
        root=etree.Element("section")
        def p(text, parent=None):
            node=etree.SubElement(parent if parent is not None else root,f"{{{HP}}}p",id="1",paraPrIDRef="4")
            run=etree.SubElement(node,f"{{{HP}}}run",charPrIDRef="7")
            # Anchors split over differently styled runs; this must survive exact replacement.
            etree.SubElement(run,f"{{{HP}}}t").text=text[:len(text)//2]
            run=etree.SubElement(node,f"{{{HP}}}run",charPrIDRef="8")
            etree.SubElement(run,f"{{{HP}}}t").text=text[len(text)//2:]
            etree.SubElement(node,f"{{{HP}}}linesegarray")
            return node
        # Header table nesting is typical; each cell contains its own paragraph.
        container=p("")
        table=etree.SubElement(container,f"{{{HP}}}tbl")
        for fragment in item["title_fragments"]: p("제목: "+fragment,table)
        p("수신: "+item["receiver"],table)
        body_parent=None
        if item["family"]!="공문":
            # 업무연락 and guideline samples put their entire body in a shape's subList.
            shape=p("")
            body_parent=etree.SubElement(shape,f"{{{HP}}}subList")
            p("글상자 머리말 유지",body_parent)
        p(item["body_start"],body_parent)
        oldtable=p("old body table",body_parent)
        p("PAST PROJECT MUST DISAPPEAR",etree.SubElement(oldtable,f"{{{HP}}}tbl"))
        p("붙임 OLD ATTACHMENT",body_parent)
        p(item["signature"])
        p(item["old_number"])
        if item["old_date"]:p(item["old_date"])
        p("담당 : "+item["old_sender"]+" 전화 02-000-0000")
        p("기관 주소 / 연락처")
        src=self.base/(item["id"]+".hwpx")
        with zipfile.ZipFile(src,"w") as z:
            z.writestr("Contents/section0.xml",etree.tostring(root))
            z.writestr("Contents/header.xml",b"<styles/>")
            z.writestr("BinData/logo.png",b"original logo")
            z.writestr("Preview/PrvText.txt",b"OLD PREVIEW")
            z.writestr("Preview/PrvImage.png",b"OLD IMAGE")
        return src

    def values(self):
        return dict(title="새 공문 제목(주소 포함)",receiver="새 수신기관",sender="새 담당자",body="1. 회신 내용\n\n2. 확인 결과\n붙임 새 확인서 1부. 끝.",number_date="서울센터 2026-438",display_date="(2026. 11. 02.)")

    def test_all_six_real_templates_and_anchors(self):
        from core import extract
        for item in docs.catalog():
            text=extract(docs.template_root()/item["file"])
            for anchor in (*item["title_fragments"],item["body_start"],item["signature"],item["old_number"],item["old_sender"]):
                self.assertIn(docs._compact(anchor),docs._compact(text))

    def test_six_styles_replace_body_tables_title_receiver_date_keep_styles(self):
        for item in docs.catalog():
            with self.subTest(style=item["id"]):
                src=self.fixture(item);before=src.read_bytes()
                result=self.base/(item["id"]+"-filled.hwpx")
                docs.prepare_hwpx(src,result,item,self.values())
                with zipfile.ZipFile(result) as z:
                    root=etree.fromstring(z.read("Contents/section0.xml"))
                    text="\n".join(docs._text(p) for p in root.iter(f"{{{HP}}}p"))
                    self.assertIn(self.values()["title"],text)
                    self.assertIn("담당 : 새 담당자 전화",text)
                    self.assertIn("2. 확인 결과",text)
                    self.assertNotIn(item["body_start"],text)
                    self.assertNotIn("PAST PROJECT",text)
                    self.assertNotIn("OLD ATTACHMENT",text)
                    self.assertNotIn(item["old_number"],text)
                    self.assertIn(item["signature"],text)
                    self.assertEqual(z.read("BinData/logo.png"),b"original logo")
                    self.assertTrue(z.read("Preview/PrvImage.png").startswith(b"\x89PNG"))
                    self.assertFalse(list(root.iter(f"{{{HP}}}linesegarray")))
                    title=docs._find(list(root.iter(f"{{{HP}}}p")),self.values()["title"])
                    self.assertEqual(list(title)[0].get("charPrIDRef"),"7")
                self.assertEqual(before,src.read_bytes())

    def test_filenames_current_date_prefix_spacing_and_actual_document_title(self):
        for item in docs.catalog():
            name=docs.filename(item,{"title":"실제 HWP 제목","number":"9"},date(2026,11,2))
            self.assertEqual(name,f"{item['prefix']}009.26.11.02. 실제 HWP 제목{item['suffix']}.hwp")
        # Template 3's sample filename differs from the internal title: use edited HWP title.
        self.assertIn("보수·전문교육",docs.style("3")["title_fragments"][0])

    def test_defaults_and_overrides_are_independent(self):
        config={"root":str(self.base),"outgoing_folders":{"1":"D:/custom"}}
        self.assertEqual(docs.destination(config,docs.style("1")),"D:/custom")
        for key in ("2","3","6"):self.assertTrue(docs.destination(config,docs.style(key)).endswith("0.시행문(센터)"))
        self.assertTrue(docs.destination(config,docs.style("4")).endswith("0.시행문(업무연락)"))
        self.assertTrue(docs.destination(config,docs.style("5")).endswith("0.시행문(처리지침)"))

    def test_reject_unassigned_sample_numbers_conflicts_and_invalid_titles(self):
        for out in ({},{"number":"0"},{"number":"438","file_number":"437"},{"file_number":"abc"}):
            with self.assertRaises(ValueError):docs.sequence(out)
        with self.assertRaises(ValueError):docs.filename(docs.style("1"),{"title":"제목/오류","number":"438"})
        self.assertEqual(docs.sequence({"file_number":"001"}),1)

    def test_reject_changed_template_before_output(self):
        item={**docs.style("1"),"body_start":"NONEXISTENT"}
        src=self.fixture(docs.style("1"));output=self.base/"bad.hwpx"
        with self.assertRaises(ValueError):docs.prepare_hwpx(src,output,item,self.values())
        self.assertFalse(output.exists())

    def test_six_original_bodies_loaded_and_recommended_by_receipt(self):
        for item in docs.catalog():
            body=docs.template_body(item["id"])
            self.assertTrue(body.startswith(item["body_start"]))
            self.assertNotIn(item["signature"],body)
            self.assertGreater(len(body),50)
        self.assertEqual(docs.suggested_style({"category":"중앙센터 공문"})["id"],"2")
        self.assertEqual(docs.suggested_style({"category":"서울시청 공문","title":"교육 결과 보고"})["id"],"3")
        self.assertEqual(docs.suggested_style({"category":"서울시청 공문","title":"건축협의 요청"})["id"],"6")
        self.assertEqual(docs.suggested_style({},"업무연락")["id"],"4")

    def test_preserving_original_body_keeps_tables_and_only_changes_metadata(self):
        for item in docs.catalog():
            with self.subTest(style=item["id"]):
                src=self.fixture(item);dst=self.base/(item["id"]+"-keep.hwpx")
                docs.prepare_hwpx(src,dst,item,{**self.values(),"keep_template_body":True})
                with zipfile.ZipFile(dst) as z:
                    root=etree.fromstring(z.read("Contents/section0.xml"))
                    text="\n".join(docs._text(p) for p in root.iter(f"{{{HP}}}p"))
                    self.assertIn("PAST PROJECT MUST DISAPPEAR",text) # Sample table deliberately retained in this mode.
                    self.assertIn(item["body_start"],text)
                    self.assertIn(self.values()["title"],text)
                    self.assertIn("서울센터 2026-438",text)
                    self.assertNotIn("2. 확인 결과",text)
                    with zipfile.ZipFile(src) as original:
                        before=etree.fromstring(original.read("Contents/section0.xml"))
                    self.assertEqual(len(list(root.iter(f"{{{HP}}}tbl"))),len(list(before.iter(f"{{{HP}}}tbl"))))

    def test_native_flow_converts_verifies_exclusive_saves_and_closes_private_instance(self):
        item=docs.style("2");src=self.fixture(item);events=[]
        class Hancom:
            def Open(self,*args):events.append(("open",args[1]));return True
            def SaveAs(self,path,fmt,options):
                events.append(("save",fmt))
                Path(path).write_bytes(src.read_bytes() if fmt=="HWPX" else b"verified native hwp")
                return True
            def Clear(self,x):events.append(("clear",x))
            def Quit(self):events.append(("quit",))
        out={**self.values(),"number":"438","ledger_type":"공문"}
        output=self.base/docs.filename(item,out)
        text="\n".join((out["title"],docs.template_body(item["id"]),out["receiver"],out["sender"],"서울센터 "+str(date.today().year)+"-438",f"({date.today():%Y. %m. %d.})"))
        with patch("core.extract",return_value=text),patch.object(docs,"replace_native_fields"),patch('hwp_export_check.check_baseline'),patch("hwp_fidelity.verify"):
            docs.export_hwp(item,out,output,factory=Hancom)
        self.assertEqual(output.read_bytes(),b"verified native hwp")
        self.assertEqual(events[-1],("quit",))
        self.assertIn(("save","HWP"),events)
        self.assertNotIn(("save","HWPX"),events)
        self.assertTrue(out["hwp_body_paste_needed"])
        with self.assertRaises(FileExistsError):docs.export_hwp(item,out,output,factory=Hancom)
        self.assertEqual(output.read_bytes(),b"verified native hwp")

    def test_failed_native_verification_never_publishes(self):
        item=docs.style("5");src=self.fixture(item)
        class Hancom:
            def Open(self,*args):return True
            def SaveAs(self,path,fmt,opts):
                Path(path).write_bytes(src.read_bytes() if fmt=="HWPX" else b"bad");return True
            def Clear(self,x):pass
            def Quit(self):pass
        out={**self.values(),"file_number":"3","ledger_type":"처리지침"}
        output=self.base/docs.filename(item,out)
        docs.template_body(item["id"])
        with patch("core.extract",return_value="missing title and body"),patch.object(docs,"replace_native_fields"):
            with self.assertRaises(RuntimeError):docs.export_hwp(item,out,output,factory=Hancom)
        self.assertFalse(output.exists())


if __name__=="__main__":unittest.main()
