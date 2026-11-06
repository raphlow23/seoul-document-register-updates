from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from modern_ui import apply_theme
from settings_ui import open_settings


class SettingsLayoutTests(unittest.TestCase):
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
