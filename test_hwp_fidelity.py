from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import tempfile
import json
import unittest
from unittest.mock import patch

from core import extract
import hwp_documents as docs
import hwp_fidelity as fidelity


class Native:
    def __init__(self,text):
        self.text=text;self.calls=[]
        self.params=SimpleNamespace(HSet=None)
        self.HParameterSet=SimpleNamespace(HFindReplace=self.params)
        self.HAction=self
    def Run(self,name):self.calls.append(name)
    def GetDefault(self,action,params):pass
    def CreateAction(self,name):return self
    def CreateSet(self):
        self.params=SimpleNamespace(SetItem=lambda name,value:setattr(self.params,name,value))
        return self.params
    def FindDir(self,name):return 2
    def Execute(self,params):
        self.calls.append((self.params.FindString,self.params.ReplaceString))
        self.text=self.text.replace(self.params.FindString,self.params.ReplaceString)
        return False  # Hancom may return false after a wrap-around despite a completed replacement.


class FidelityTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent/'hwp-templates'
        self.enterContext(patch.object(docs,'template_root',return_value=root))
        self.enterContext(patch.object(docs,'catalog',return_value=json.loads((root/'catalog.json').read_text(encoding='utf-8'))))
        docs.template_body.cache_clear();self.addCleanup(docs.template_body.cache_clear)
    def test_all_six_original_style_slots_and_unchanged_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            for item in docs.catalog():
                source=docs.template_root()/item["file"]
                target=Path(temp)/item["file"];target.write_bytes(source.read_bytes())
                names=fidelity.verify(source,target)
                self.assertEqual(names[:9],["바탕글","본문",*[f"개요 {n}" for n in range(1,8)]])
                self.assertTrue(fidelity.snapshot(source)["tables"][25])

    def test_changed_named_style_indent_character_or_paragraph_application_is_rejected(self):
        source=docs.template_root()/docs.style("6")["file"]
        before=fidelity.snapshot(source)
        for kind in ("style","indent","character","application","paper"):
            after=deepcopy(before)
            if kind=="style":after["tables"][26][2]+=b"changed"
            elif kind=="indent":after["tables"][25][0]+=b"changed"
            elif kind=="character":after["tables"][21][0]+=b"changed"
            elif kind=="application":after["paragraphs"][0]["style"]+=1
            else:after["paper"]+=[(73,b"changed")]
            with self.subTest(kind=kind),patch.object(fidelity,"snapshot",side_effect=[before,after]):
                with self.assertRaises(ValueError):fidelity.verify("source","output")

    def test_native_field_edits_keep_original_body_and_never_apply_a_style(self):
        for item in docs.catalog():
            source=extract(docs.template_root()/item["file"])
            hwp=Native(source)
            values={"title":"새 제목(새 주소)","receiver":"새 수신기관", "sender":"새 담당자","number_date":"서울센터 2026-438","display_date":"(2026. 11. 02.)"}
            docs.replace_native_fields(hwp,item,values)
            self.assertIn(docs.template_body(item["id"]),hwp.text)
            self.assertIn("새 수신기관",hwp.text)
            self.assertIn("새 제목",hwp.text)
            self.assertTrue(all(isinstance(c,tuple) or c=="MoveDocBegin" for c in hwp.calls))
            self.assertEqual(hwp.params.ReplaceStyle,"")

    def test_new_title_quoting_old_number_or_sender_does_not_get_replaced_again(self):
        item=docs.style("2");hwp=Native(extract(docs.template_root()/item["file"]))
        title=f"{item['old_number']} 및 {item['old_sender']} 관련 회신"
        docs.replace_native_fields(hwp,item,{"title":title,"receiver":"새 수신기관","sender":"새 담당자","number_date":"서울센터 2026-438","display_date":"(2026. 11. 02.)"})
        self.assertIn(title,hwp.text)
        self.assertIn("서울센터 2026-438",hwp.text)


if __name__=="__main__":unittest.main()
