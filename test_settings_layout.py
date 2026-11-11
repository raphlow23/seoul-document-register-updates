from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import Mock
import unittest
from modern_ui import apply_theme
from settings_ui import open_settings


class SettingsLayoutTests(unittest.TestCase):
    def controls(self,widget):
        result=[widget] if isinstance(widget,ttk.Button) else []
        return result+[b for child in widget.winfo_children() for b in self.controls(child)]

    def test_current_defaults_are_visible_without_freezing_inherited_connections(self):
        from sheet_connection_ui import open_defaults
        from sheet_connections import FAMILIES,SCOPES,default_connection
        self.app.config={'spreadsheet_id':'fixture-main','tab':'공문 수신 기본탭','ledger_connections':{'outgoing:업무연락':{'tab':'별도 업무연락 발행'}}}
        original=deepcopy(self.app.config);win=open_defaults(self.app);self.root.update()
        for family in FAMILIES:
            for scope in SCOPES:
                values=win.connection_fields[scope+':'+family];target=default_connection(original,scope,family)
                self.assertEqual(values['spreadsheet_id'].get(),'https://docs.google.com/spreadsheets/d/fixture-main/edit')
                self.assertEqual(values['tab'].get(),target['tab'])
        self.assertEqual(self.app.config,original);self.assertTrue(self.app.commands.empty())
        next(b for b in self.controls(win) if b.cget('text')=='기본 연결 저장').invoke()
        self.assertEqual(self.app.config,original)

    def test_editing_one_displayed_connection_changes_only_that_role(self):
        from sheet_connection_ui import open_defaults
        self.app.config={'spreadsheet_id':'fixture-main','ledger_connections':{}}
        win=open_defaults(self.app)
        values=win.connection_fields['stamp:처리지침'];values['spreadsheet_id'].set('https://docs.google.com/spreadsheets/d/new-stamp/edit');values['tab'].set('다른 직인대장')
        next(b for b in self.controls(win) if b.cget('text')=='기본 연결 저장').invoke()
        self.assertEqual(self.app.config['ledger_connections'],{'stamp:처리지침':{'spreadsheet_id':'new-stamp','tab':'다른 직인대장'}})
        self.assertEqual(self.app.config['spreadsheet_id'],'fixture-main')

    def test_read_check_uses_fallback_after_clearing_an_override(self):
        from sheet_connection_ui import open_defaults
        self.app.config={'spreadsheet_id':'fixture-main','ledger_connections':{'receipt:공문':{'spreadsheet_id':'old-private','tab':'별도 수신탭'}}}
        self.app.send=Mock(return_value=True);original=deepcopy(self.app.config)
        win=open_defaults(self.app);win.connection_fields['receipt:공문']['spreadsheet_id'].set('')
        next(b for b in self.controls(win) if b.cget('text')=='읽기 확인').invoke()
        self.assertEqual(self.app.send.call_args.args[1]['target'],{'spreadsheet_id':'fixture-main','tab':'별도 수신탭'})
        self.assertEqual(self.app.config,original)

    def setUp(self):
        self.root=tk.Tk();apply_theme(self.root);self.root.geometry('1000x700');self.addCleanup(self.close)
        self.app=SimpleNamespace(root=self.root,config={},update_status=tk.StringVar(),status=tk.StringVar(),commands=queue.Queue(),workflow=SimpleNamespace(available=lambda:True),persist_config=lambda:None)

    def close(self):
        self.root.update_idletasks()
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def test_footer_buttons_are_not_clipped_in_short_settings_window(self):
        win=open_settings(self.app);win.geometry('760x480');self.root.update()
        footer=win.settings_parts['footer']
        for button in footer.winfo_children():
            self.assertGreaterEqual(button.winfo_height(),button.winfo_reqheight())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),win.winfo_rooty()+win.winfo_height())
        self.assertEqual([b.cget('text') for b in footer.winfo_children()],['업데이트 확인','설정 저장','닫기'])
        panel=win.settings_parts['basic'];self.assertGreater(panel.body.winfo_reqheight(),panel.canvas.winfo_height())
        panel.canvas.yview_moveto(1);self.assertGreater(panel.canvas.yview()[0],0)
        win.settings_parts['tabs'].select(1);self.root.update()
        panel=win.settings_parts['advanced'];panel.canvas.yview_moveto(1)
        self.assertGreater(panel.canvas.yview()[0],0);win.destroy()

    def test_nine_ledger_connection_cards_scroll_and_keep_save_visible(self):
        from sheet_connection_ui import open_defaults
        win=open_defaults(self.app);win.geometry('760x480');self.root.update()
        self.assertEqual(len(win.connection_fields),9)
        footer=win.winfo_children()[0]
        for button in footer.winfo_children():
            self.assertGreaterEqual(button.winfo_height(),button.winfo_reqheight())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),win.winfo_rooty()+win.winfo_height())
        win.destroy()

    def test_document_connection_controls_respect_attempted_registration(self):
        from sheet_connection_ui import open_document
        self.app.config={'spreadsheet_id':'fixture-sheet'}
        self.app.jobs={'fixture':{'hash':'fixture','files':[],'receipt_type':'공문'}}
        win=open_document(self.app,'fixture','receipt','공문');self.root.update()
        self.assertEqual(win.connection_fields['spreadsheet_id'].get(),'fixture-sheet');win.destroy()
        self.app.jobs['fixture']['sheet_attempted']=True
        win=open_document(self.app,'fixture','receipt','공문');self.root.update()
        def entries(widget):
            return [widget] if isinstance(widget,ttk.Entry) else [e for child in widget.winfo_children() for e in entries(child)]
        self.assertTrue(all(e.instate(['readonly']) for e in entries(win)));win.destroy()
