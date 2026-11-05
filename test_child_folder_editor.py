import tempfile
from pathlib import Path
from types import SimpleNamespace
import queue
import tkinter as tk
import unittest
from unittest.mock import patch,Mock
from child_folder_editor import ChildFolderEditor
from folder_naming import child_folders,remember_profile,describe_directory


class ChildFolderTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.close_root)

    def close_root(self):
        self.root.update_idletasks()
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def test_defaults_are_protected_custom_folder_can_be_added_reordered_and_renumbered(self):
        saved=Mock();dialog=ChildFolderEditor(self.root,'중앙센터 공문',['incoming','outgoing','reply'],['incoming','reply'],saved)
        with patch('child_folder_editor.simpledialog.askstring',return_value='검토자료'):dialog.add()
        dialog.move(-1);dialog.move(-1);dialog.move(-1)
        names=child_folders('중앙센터 공문',[k for k in dialog.order if k in dialog.selected],dialog.order)
        self.assertEqual(list(names.values()),['1.검토자료','2.중앙공문','3.중앙회신'])
        dialog.tree.selection_set('incoming')
        with patch('child_folder_editor.messagebox.showinfo'):dialog.remove()
        self.assertIn('incoming',dialog.order);dialog.apply()
        self.assertEqual(saved.call_args.args[0],['custom:검토자료','incoming','outgoing','reply'])

    def test_reordering_defaults_without_custom_names_uses_explicit_order(self):
        self.assertEqual(list(child_folders('서울시청 공문',['reply','incoming'],['reply','incoming','outgoing']).values()),['1.시청회신','2.시청공문'])
        self.assertEqual(list(child_folders('서울시청 공문',['reply','incoming']).values()),['1.시청공문','2.시청회신'])

    def test_receipt_and_outgoing_choosers_retain_custom_order(self):
        from route_chooser import RouteChooser
        from outgoing_folder_chooser import OutgoingFolderChooser
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'0.중앙센터 공문';folder.mkdir()
            order=['reply','custom:검토자료','incoming','outgoing'];selected=['reply','custom:검토자료','incoming']
            config={'root':temp};remember_profile(config,'incoming:0.중앙센터 공문',folder,'{number:03d}. {title}','중앙센터 공문',selected,'incoming',order)
            app=SimpleNamespace(root=self.root,config=config,commands=queue.Queue(),busy=False)
            job={'hash':'fixture','title':'시험','date':'2026-11-05','category':'중앙센터 공문','files':[]}
            dialog=RouteChooser(app,job);dialog.win.after_cancel(dialog.initial_after);dialog.request='fixture'
            dialog.deliver({'owner':dialog.owner,'request':'fixture','records':[{**describe_directory(folder),'relative':'0.중앙센터 공문'}]})
            dialog.selected.add('0.중앙센터 공문');dialog.redraw_preview()
            self.assertIn('1.중앙회신',dialog.preview.get('1.0','end'));self.assertIn('2.검토자료',dialog.preview.get('1.0','end'))
            dialog.apply(False);routing=app.commands.get_nowait()[1]['routing'];self.assertEqual(routing['targets'][0]['child_order'],order)
            out={'template_id':'1','title':'시험','save_folder':str(folder),'date':'2026-11-05','folder_plan':{'base':str(folder),'agency':'중앙센터 공문','children':selected,'child_order':order,'save_in':'custom:검토자료'}}
            outgoing=OutgoingFolderChooser(app,job,out,Mock());outgoing.win.after_cancel(outgoing.initial)
            outgoing.record=describe_directory(folder);outgoing.redraw()
            self.assertEqual(outgoing.plan()['child_order'],order);self.assertEqual(outgoing.names['custom:검토자료'],'2.검토자료');outgoing.close()
