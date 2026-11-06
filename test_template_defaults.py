from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hwp_documents as docs


class DefaultTemplateTests(unittest.TestCase):
    def test_seven_current_types_keep_stable_ids(self):
        with tempfile.TemporaryDirectory() as base,patch.object(docs,'template_root',return_value=Path(base)):
            items=docs.catalog()
            self.assertEqual([x['id'] for x in items],['7','1','2','3','4','5','6','8'])
            self.assertEqual([x['label'] for x in items][:6],['센터회신','센터발송','중앙회신','시청회신','업무연락','처리지침'])
            self.assertTrue(all(x['blank_body'] and x['metadata_only'] for x in items))
            self.assertEqual(docs.suggested_style({'category':'기초센터 공문'})['id'],'7')
            self.assertEqual(docs.suggested_style({'category':'중앙센터 공문'})['id'],'2')
            self.assertEqual(docs.suggested_style({'category':'시청공문','title':'건축허가 협의'})['id'],'6')
            self.assertEqual(docs.suggested_style({'category':'완화공문'})['id'],'8')
            self.assertEqual(items[-1]['family'],'공문');self.assertEqual(items[-1]['folder'],'3.완화신청')
