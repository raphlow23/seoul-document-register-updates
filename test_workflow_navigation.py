import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from workflow_ui import WorkflowShell


class NavigationTests(unittest.TestCase):
    def shell(self,section,step,job):
        shell=WorkflowShell.__new__(WorkflowShell)
        shell.section=section;shell.step=step;shell.current='fixture';shell.available=lambda:True
        shell.app=SimpleNamespace(jobs={'fixture':job},settings=Mock(),status=Mock())
        for name in ('title_step','start_outgoing','outgoing_form','receipt_home','outgoing_home','history_home'):setattr(shell,name,Mock())
        return shell

    def test_receipt_can_return_before_side_effects(self):
        shell=self.shell('receipt',3,{'hash':'fixture'})
        shell.back();shell.title_step.assert_called_once()

    def test_receipt_keeps_transaction_after_writes_started(self):
        for marker in ('files_started','sheet_attempted'):
            job={'hash':'fixture',marker:True,'number':'581','title':'기존 제목'}
            shell=self.shell('receipt',3,job);shell.back()
            shell.app.settings.assert_called_once();shell.title_step.assert_not_called()
            self.assertEqual(job['number'],'581');self.assertTrue(job[marker])

    def test_outgoing_returns_to_picker_only_before_registration(self):
        job={'hash':'fixture','outgoing':{'title':'수정 제목'}}
        shell=self.shell('outgoing',3,job);shell.back();shell.start_outgoing.assert_called_once_with(job)
        job['outgoing'].update(sheet_attempted=True,number='12')
        shell=self.shell('outgoing',3,job);shell.back()
        shell.app.settings.assert_called_once();shell.start_outgoing.assert_not_called();self.assertEqual(job['outgoing']['number'],'12')

    def test_completed_outgoing_goes_to_existing_form(self):
        job={'hash':'fixture','outgoing':{'number':'12','sheet_attempted':True}}
        shell=self.shell('outgoing',4,job);shell.back();shell.outgoing_form.assert_called_once_with(job)

    def test_reconfigure_keeps_registered_records(self):
        for section in ('receipt','outgoing'):
            job={'hash':'fixture','receipt_done':True,'outgoing':{'sheet_attempted':True,'number':'12'}}
            shell=self.shell(section,3,job);shell.reconfigure();shell.app.settings.assert_called_once()
            self.assertEqual(job['outgoing']['number'],'12')


if __name__=='__main__':unittest.main()
