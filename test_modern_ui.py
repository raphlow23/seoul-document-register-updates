import tkinter as tk
from tkinter import ttk
import unittest
from modern_ui import apply_theme,CheckboxTree,ScrollablePanel


class ModernUiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.close)
        apply_theme(self.root)

    def close(self):
        self.root.update_idletasks()
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def test_rounded_native_controls_keep_check_and_radio_actions(self):
        value=tk.BooleanVar(value=False);check=ttk.Checkbutton(self.root,variable=value)
        check.invoke();self.assertTrue(value.get());check.invoke();self.assertFalse(value.get())
        choice=tk.StringVar(value='a');radio=ttk.Radiobutton(self.root,variable=choice,value='b')
        radio.invoke();self.assertEqual(choice.get(),'b')
        self.assertEqual(apply_theme(self.root).theme_use(),'seoul-modern')

    def test_checkbox_tree_uses_check_images_in_main_and_dialog_windows(self):
        for parent in (self.root,tk.Toplevel(self.root)):
            tree=CheckboxTree(parent);tree.insert('','end',iid='a',text='☐ 저장 위치')
            before=tree.item('a','image');self.assertTrue(before);self.assertEqual(tree.item('a','text'),'저장 위치')
            tree.item('a',text='☑ 저장 위치');self.assertNotEqual(tree.item('a','image'),before)
            tree.destroy()
            if parent is not self.root:parent.destroy()

    def test_long_settings_panel_remains_scrollable(self):
        panel=ScrollablePanel(self.root);panel.pack()
        for i in range(20):ttk.Label(panel.body,text='설정 '+str(i)).pack()
        self.root.update_idletasks()
        self.assertGreater(panel.body.winfo_reqheight(),panel.canvas.winfo_height())
        panel.canvas.yview_moveto(1);self.assertGreater(panel.canvas.yview()[0],0)
