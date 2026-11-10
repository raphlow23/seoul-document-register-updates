from pathlib import Path
import queue,tempfile,tkinter as tk,unittest
from types import SimpleNamespace
from modern_ui import apply_theme
from title_chooser import TitleChooser


class TitleLayoutTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.root=tk.Tk();apply_theme(self.root);self.root.geometry('1000x660');self.addCleanup(self.close)
        parent=tk.Frame(self.root);parent.pack(fill='both',expand=True)
        fields={k:tk.StringVar(value='') for k in ('category','receipt_type','title','date','recipient','issuer')}
        self.app=SimpleNamespace(root=self.root,fields=fields,commands=queue.Queue(),busy=False,workflow=SimpleNamespace(receipt_home=lambda:None))
        path=self.base/'fixture.pdf';path.write_bytes(b'fixture')
        self.editor=TitleChooser(self.app,[{'hash':'fixture','title':'fixture','category':'','files':[{'hash':'file','path':str(path)}]}],parent)
        self.editor.win.after_cancel(self.editor.initial_after);self.editor.request='fixture'
        self.editor.deliver({'owner':self.editor.owner,'request':'fixture','text':'공문 본문입니다.\n'*100,'image':None,'page_count':1,
                             'options':[{'label':'제목 후보','title':'긴 제목입니다. '*15,'mode':'header'} for _ in range(6)]})
        self.root.update()

    def close(self):
        for token in self.root.tk.call('after','info'):self.root.after_cancel(token)
        self.root.destroy()

    def test_body_keeps_readable_height_even_with_many_long_title_suggestions(self):
        self.assertGreaterEqual(self.editor.body.winfo_height(),180)
        self.assertLessEqual(self.editor.title.winfo_height(),100)
        self.assertGreater(self.editor.body.winfo_height(),self.editor.title.winfo_height())
        self.assertEqual(len(self.editor.option_box['values']),6)

    def test_expanded_body_keeps_text_and_selected_sentence_can_become_title(self):
        win=self.editor.expand_body();win.geometry('800x550');self.root.update()
        text=self.editor.expanded_body;self.assertGreater(text.winfo_height(),300)
        self.assertEqual(text.get('1.0','end-1c'),self.editor.body.get('1.0','end-1c'))
        text.tag_add('sel','1.0','1.end');self.editor.use_selection(text)
        self.assertEqual(self.editor.title.get('1.0','end-1c'),'공문 본문입니다.')
        self.assertEqual(self.editor.mode,'selection');win.destroy()
