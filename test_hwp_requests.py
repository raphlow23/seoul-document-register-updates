from pathlib import Path
from types import SimpleNamespace
import queue,threading,unittest
from unittest.mock import Mock,patch
from app import App


class HwpRequestTests(unittest.TestCase):
    def app(self):
        event=threading.Event();event.set()
        return SimpleNamespace(worker=SimpleNamespace(working=event,active_command='watch',active_document=None),commands=queue.Queue(),status=Mock(),busy=True,updating=False,startup_gate=False)

    def test_background_busy_queues_hwp_once_without_modal_or_ledger_registration(self):
        app=self.app();data={'hash':'document','native_hwp':True}
        with patch('app.messagebox.showinfo') as modal:
            self.assertTrue(App.queue_hwp_export(app,data));self.assertTrue(App.queue_hwp_export(app,data))
        modal.assert_not_called();self.assertEqual(app.commands.get_nowait(),('draft_export',data));self.assertTrue(app.commands.empty())

    def test_active_export_does_not_enqueue_duplicate(self):
        app=self.app();app.worker.active_command='draft_export';app.worker.active_document='document'
        App.queue_hwp_export(app,{'hash':'document'});self.assertTrue(app.commands.empty())

    def test_repeated_open_never_opens_a_partially_written_target(self):
        from workflow_ui import WorkflowShell
        shell=WorkflowShell.__new__(WorkflowShell);shell.app=self.app();shell.app.hwp_pending={'document'}
        with patch('workflow_ui.os.startfile') as open_file:shell.export_registered({'hash':'document','outgoing':{}})
        open_file.assert_not_called()

    def test_startup_only_starts_watch_without_shared_folder_maintenance(self):
        app=SimpleNamespace(watching=Mock(),commands=queue.Queue())
        App.start_watch(app);self.assertEqual(app.commands.get_nowait(),('watch',True));self.assertTrue(app.commands.empty())

    def test_watch_does_not_block_normal_commands_but_registration_does(self):
        app=self.app();app.busy=False;app.worker.active_command=None
        with patch('app.messagebox.showinfo') as modal:self.assertTrue(App.send(app,'sheet_override',{}))
        modal.assert_not_called();self.assertEqual(app.commands.get_nowait(),('sheet_override',{}))
        app.busy=False;app.worker.active_command='process_outgoing'
        with patch('app.messagebox.showinfo') as modal:self.assertFalse(App.send(app,'sheet_override',{}))
        modal.assert_called_once();self.assertTrue(app.commands.empty())


if __name__=='__main__':unittest.main()
