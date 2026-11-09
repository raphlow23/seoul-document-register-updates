from copy import deepcopy
from pathlib import Path
import tempfile,unittest
from unittest.mock import Mock
import core,outgoing
from outgoing_new import create
from outgoing_restart import restart


class NewOutgoingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        self.config={'root':str(self.base),'recipient':'기본 담당자','_program_dir':str(self.base/'Program')}

    def ready(self):
        job=create(self.config,self.store)
        edited=deepcopy(job['outgoing']);edited.update(title='새 안내 공문',receiver='수신 기관')
        outgoing.merge_edit(job,edited);self.store.put(job);return job

    def test_new_drafts_have_no_receipt_or_attachment_and_distinct_identities(self):
        first=create(self.config,self.store);second=create(self.config,self.store)
        self.assertNotEqual(first['hash'],second['hash']);self.assertNotEqual(first['outgoing']['id'],second['outgoing']['id'])
        self.assertFalse(first['files']);self.assertFalse(first['outgoing']['title_same'])
        for key in ('receipt_done','number','row','receipt_type','sheet_attempted'):self.assertNotIn(key,first)
        self.assertEqual(first['outgoing']['sender'],'기본 담당자')
        self.assertEqual(first['outgoing']['stamp']['department'],'편의증진부');self.assertEqual(first['outgoing']['stamp']['person'],'')

    def test_outgoing_and_stamp_register_without_receipt_sheet_or_file_copy(self):
        job=self.ready();calls=[]
        class Out:
            def __init__(self,config):pass
            def register(self,job,store):calls.append('outgoing');return 8,'445'
        class Stamp:
            def __init__(self,config):pass
            def register(self,job,store):calls.append('stamp');return 10,'433'
        forbidden=Mock(side_effect=AssertionError('Receipt must never run'))
        result=core.process(job,self.config,self.store,forbidden,Out,Stamp)
        self.assertEqual(calls,['outgoing','stamp']);forbidden.assert_not_called()
        self.assertEqual(result['outgoing']['number'],'445');self.assertEqual(result['title'],'새 안내 공문')
        self.assertEqual(result['state'],'완료');self.assertNotIn('receipt_done',result);self.assertNotIn('number',result)
        core.process(result,self.config,self.store,forbidden,Out,Stamp);self.assertEqual(calls,['outgoing','stamp'])

    def test_failed_stamp_resumes_without_duplicate_outgoing_or_fake_receipt(self):
        job=self.ready();calls=[]
        class Out:
            def __init__(self,config):pass
            def register(self,job,store):calls.append('outgoing');return 8,'445'
        class Stamp:
            def __init__(self,config):pass
            def register(self,job,store):
                calls.append('stamp')
                if calls.count('stamp')==1:raise ConnectionError('fixture interrupted')
                return 10,'433'
        with self.assertRaises(ConnectionError):core.process(job,self.config,self.store,outgoing_factory=Out,stamp_factory=Stamp)
        resumed=self.store.get(job['hash']);core.process(resumed,self.config,self.store,outgoing_factory=Out,stamp_factory=Stamp)
        self.assertEqual(calls,['outgoing','stamp','stamp']);self.assertNotIn('receipt_done',resumed)

    def test_restart_and_hide_draft_preserve_outgoing_only_identity(self):
        job=self.ready();old=job['outgoing']['id']
        hidden=restart(job,self.config,self.store,hide=True)
        self.assertTrue(hidden['outgoing_list_hidden']);self.assertNotEqual(hidden['outgoing']['id'],old)
        self.assertNotIn('receipt_done',hidden);self.assertNotIn('number',hidden)
        fresh=restart(hidden,self.config,self.store)
        self.assertNotIn('outgoing_list_hidden',fresh);self.assertFalse(fresh['outgoing']['title_same'])

    def test_empty_title_is_rejected_before_registering(self):
        job=create(self.config,self.store);job['outgoing']['receiver']='수신 기관'
        with self.assertRaisesRegex(ValueError,'발신 제목'):outgoing.validate(job,self.config)
