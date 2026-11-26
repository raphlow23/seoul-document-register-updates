import tkinter as tk
from tkinter import ttk
import unittest
from modern_ui import BG,WHITE,apply_theme,ask_text,CheckboxTree,ScrollablePanel


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

    def test_rounded_tabs_and_header_buttons_use_one_outer_element(self):
        style=ttk.Style(self.root)
        for name,element in [('TNotebook.Tab','Modern.Tab'),('Header.TButton','Modern.Header'),
                             ('Header.NavSelected.TButton','Modern.HeaderSelected'),('Accent.TButton','Modern.Accent')]:
            self.assertEqual([part for part,_ in style.layout(name)],[element])
        tabs=ttk.Notebook(self.root)
        for name in ('발신 내용','저장 위치'):tabs.add(ttk.Frame(tabs),text=name)
        tabs.select(1)
        self.assertEqual(tabs.index('current'),1)

    def test_long_settings_panel_remains_scrollable(self):
        panel=ScrollablePanel(self.root);panel.pack()
        for i in range(20):ttk.Label(panel.body,text='설정 '+str(i)).pack()
        self.root.update_idletasks()
        self.assertGreater(panel.body.winfo_reqheight(),panel.canvas.winfo_height())
        panel.canvas.yview_moveto(1);self.assertGreater(panel.canvas.yview()[0],0)

    def test_corners_use_their_menu_surface_in_all_states(self):
        style=ttk.Style(self.root)
        for name in ('TNotebook','TLabelframe'):
            self.assertEqual(style.layout(name)[0][0],'Modern.Group')
        for name in ('TButton','Accent.TButton','Header.TButton','Header.NavSelected.TButton',
                     'TCheckbutton','TRadiobutton','TNotebook.Tab','TEntry','TCombobox'):
            for state in ('active','pressed','focus','disabled','selected'):
                self.assertEqual(style.lookup(name,'background',(state,)),BG if name.startswith('Header.') else WHITE)

    def test_rename_prompt_uses_themed_buttons_and_restores_parent_grab(self):
        editor=tk.Toplevel(self.root)
        editor.update()
        editor.grab_set()
        def accept():
            dialog=editor.winfo_children()[0]
            dialog.entry.delete(0,'end')
            dialog.entry.insert(0,'새 양식')
            buttons=dialog.winfo_children()[-1].winfo_children()
            self.assertTrue(all(isinstance(b,ttk.Button) for b in buttons))
            buttons[0].invoke()
        editor.after(100,accept)
        self.assertEqual(ask_text('이름 변경','양식 이름',parent=editor,initialvalue='기본 양식'),'새 양식')
        self.assertEqual(editor.grab_current(),editor)
