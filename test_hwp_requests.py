from pathlib import Path
from types import SimpleNamespace
import queue,threading,unittest
from unittest.mock import Mock,patch
from app import App


class HwpRequestTests(unittest.TestCase):
    def test_custom_file_name_keeps_native_title_and_number_independent(self):
        import hwp_documents as docs
        item={'prefix':'','suffix':'','family':'공문','blank_body':True}
        out={'title':'협의/회신: 건축허가?','number':'438'}
        self.assertIn('협의_회신_ 건축허가_',docs.filename(item,out))
        out['filename_override']='파일용 이름'
        self.assertEqual(docs.filename(item,out),'파일용 이름.hwp')
        self.assertEqual(out['title'],'협의/회신: 건축허가?')
        self.assertEqual(docs.sequence(out),438)
        for bad in ('../다른위치.hwp','이름:오류','CON.hwp','.hwp'):
            out['filename_override']=bad
            with self.assertRaises(ValueError):docs.filename(item,out)

    def test_filename_override_is_retained_without_changing_registered_title(self):
        from outgoing import merge_edit
        old={'enabled':True,'ledger_type':'공문','title':'등록 제목/원문','title_same':False,'date':'2026-11-11','receiver':'기관','sender':'담당','kind':'발송문서','method':'Email','sheet_attempted':True,'complete':True,'number':'438','document_number':'서울센터2026-438','stamp':{}}
        job={'state':'완료','outgoing':old}
        merge_edit(job,{**old,'filename_override':'파일만 별도'})
        self.assertEqual(job['outgoing']['title'],'등록 제목/원문')
        self.assertEqual(job['outgoing']['filename_override'],'파일만 별도')

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
