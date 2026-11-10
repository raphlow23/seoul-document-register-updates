from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest
from unittest.mock import Mock,patch
import hwp_fidelity as fidelity
from hwp_export_check import verify_export
import hwp_documents as docs


class ExportCheckTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.paths=[self.base/name for name in ('original.hwp','unchanged.hwp','result.hwp')]
        for path in self.paths:path.write_bytes(path.name.encode())
        self.state={'tables':{i:[b'original'] for i in range(19,27)},'names':['바탕글','본문',*[f'개요 {i}' for i in range(1,9)]],
                    'paragraphs':[{'paragraph':0,'style':1,'characters':[0]}],'paper':[(73,b'paper')]}
        self.states={p.name:deepcopy(self.state) for p in self.paths}
        self.states['unchanged.hwp']['tables'][19]=[b'current Hancom font serialization']
        self.states['result.hwp']=deepcopy(self.states['unchanged.hwp'])
        self.texts={p.name:'원본 양식 본문' for p in self.paths}
        self.enterContext(patch('hwp_fidelity.snapshot',side_effect=lambda p:deepcopy(self.states[Path(p).name])))
        self.enterContext(patch('core.extract',side_effect=lambda p:self.texts[Path(p).name]))
        self.enterContext(patch('program_storage.temporary_root',return_value=self.base/'private'))

    def test_version_serialization_is_compared_to_unedited_same_version_baseline(self):
        names=verify_export(*self.paths,fidelity.verify)
        self.assertEqual(names,self.state['names']);self.assertFalse((self.base/'private').exists())
        self.assertEqual(self.paths[0].read_bytes(),b'original.hwp')

    def test_actual_font_character_paragraph_style_and_paper_changes_are_rejected(self):
        for tag in (19,21,25,26):
            original=deepcopy(self.states['result.hwp']);self.states['result.hwp']['tables'][tag]=[b'changed formatting']
            with self.subTest(tag=tag),self.assertRaises(ValueError):verify_export(*self.paths,fidelity.verify)
            self.states['result.hwp']=original
        self.states['result.hwp']['paper']=[(73,b'changed size')]
        with self.assertRaises(ValueError):verify_export(*self.paths,fidelity.verify)

    def test_changed_baseline_style_order_and_body_are_not_accepted_as_normalization(self):
        verify=Mock()
        self.states['unchanged.hwp']['names'].reverse()
        with self.assertRaisesRegex(ValueError,'스타일 이름'):verify_export(*self.paths,verify)
        verify.assert_not_called()
        self.states['unchanged.hwp']['names']=self.state['names'].copy();self.texts['unchanged.hwp']='누락된 본문'
        with self.assertRaisesRegex(ValueError,'내용이'):verify_export(*self.paths,verify)

    def test_style_failure_keeps_private_evidence_and_never_overwrites_original(self):
        original=self.paths[0].read_bytes()
        self.states['result.hwp']['paragraphs'][0]['style']=3
        with self.assertRaisesRegex(ValueError,'확인 자료'):verify_export(*self.paths,fidelity.verify)
        folders=list((self.base/'private').iterdir());self.assertEqual(len(folders),1)
        for name in ('original.hwp','unchanged.hwp','result.hwp','report.json'):self.assertTrue((folders[0]/name).is_file())
        self.assertEqual(self.paths[0].read_bytes(),original)

    def test_fresh_replace_sets_never_read_user_formatting_preferences(self):
        params=[];executed=[]
        class Action:
            def CreateSet(self):
                values={};params.append(values);return SimpleNamespace(SetItem=values.__setitem__)
            def Execute(self,values):executed.append(values);return False
        native=SimpleNamespace(CreateAction=lambda name:Action(),FindDir=lambda direction:2,HAction=SimpleNamespace(Run=Mock(),GetDefault=Mock(side_effect=AssertionError('must not import saved preferences'))))
        item={'file':'original.hwp','title_fragments':['제목'],'receiver':'수신','old_number':'번호','old_sender':'담당','old_date':'','metadata_only':False}
        with patch.object(docs,'template_root',return_value=self.base),patch('core.extract',return_value='제목 수신 번호 담당'):
            docs.replace_native_fields(native,item,{'title':'새 제목','receiver':'새 기관','number_date':'서울센터 2026-452','sender':'새 담당'})
        self.assertEqual(len(params),4);self.assertEqual(len(executed),4)
        self.assertTrue(all('ReplaceCharShape' not in p and 'ReplaceParaShape' not in p for p in params))
        self.assertTrue(all(p['IgnoreMessage']==1 for p in params))


if __name__=='__main__':unittest.main()
