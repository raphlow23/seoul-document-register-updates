from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import tkinter as tk
import struct
import zlib

import template_library as library
import hwp_documents as docs
from hwp_anchor_slots import occurrences


class AnchorSlotTests(unittest.TestCase):
    def fixture(self,base):
        import pythoncom
        from win32com import storagecon
        def record(tag,payload):return struct.pack('<I',tag|(len(payload)<<20))+payload
        def pack(raw):
            comp=zlib.compressobj(wbits=-15);return comp.compress(raw)+comp.flush()
        header=bytearray(256);header[:17]=b'HWP Document File';struct.pack_into('<I',header,36,1)
        styles=b''.join(record(26,struct.pack('<H',len(name))+name.encode('utf-16-le')) for name in ['Style'+str(i) for i in range(10)])
        lines=['수신 기초센터장','제목 시험 문서 제목','본문 다른 기관 (2026. 00. 00.) 참조','서울센터 2026-000','시행 (2026. 00. 00.)']
        section=b''.join(record(66,b'\0'*8+struct.pack('<H',4)+b'\x03\0')+record(67,(line+'\r').encode('utf-16-le'))+record(68,struct.pack('<II',0,7)) for line in lines)
        path=base/'fixture.hwp';pythoncom.CoInitialize();storage=body=stream=None
        try:
            mode=storagecon.STGM_READWRITE|storagecon.STGM_SHARE_EXCLUSIVE
            storage=pythoncom.StgCreateDocfile(str(path),mode|storagecon.STGM_CREATE,0)
            for name,data in [('FileHeader',bytes(header)),('DocInfo',pack(styles)),('PrvImage',b'preserved image')]:
                stream=storage.CreateStream(name,mode|storagecon.STGM_CREATE,0,0);stream.Write(data);stream.Commit(0);stream=None
            body=storage.CreateStorage('BodyText',mode|storagecon.STGM_CREATE,0,0)
            stream=body.CreateStream('Section0',mode|storagecon.STGM_CREATE,0,0);stream.Write(pack(section));stream.Commit(0);stream=None
            body.Commit(0);body=None;storage.Commit(0);storage=None
        finally:
            stream=body=storage=None;pythoncom.CoUninitialize()
        return path

    def test_repeated_date_only_changes_the_selected_metadata_location(self):
        from core import extract
        from hwp_fidelity import verify
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=self.fixture(base);original=source.read_bytes();text=extract(source)
            settings={**library.suggestions(text),'label':'원본 직접 등록','family':'공문','folder':str(base/'output'),'_occurrences':{'date':1}}
            with patch.object(docs,'catalog',return_value=[]):item=library.add(source,settings,base/'private')
            copy=base/'private'/item['file'];after=extract(copy)
            self.assertEqual(after.count('(2026. 00. 00.)'),1)
            self.assertIn('본문 다른 기관 (2026. 00. 00.) 참조',after)
            self.assertIn(item['old_date'].rstrip(),after);self.assertEqual(source.read_bytes(),original)
            self.assertEqual(len(verify(source,copy)),10)
            with patch.object(docs,'template_root',return_value=copy.parent):docs.check_template(item['id'])

    def test_ambiguous_date_does_not_register_or_modify_source(self):
        from core import extract
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=self.fixture(base);original=source.read_bytes()
            settings={**library.suggestions(extract(source)),'label':'잘못된 위치','family':'공문','folder':str(base/'output')}
            with self.assertRaises(ValueError):library.add(source,settings,base/'private')
            self.assertFalse((base/'private').exists());self.assertEqual(source.read_bytes(),original)

    def test_occurrences_include_context_and_exact_offset(self):
        text='본문 (2026. 00. 00.) 참조\n시행 (2026. 00. 00.)'
        found=occurrences(text,'(2026. 00. 00.)')
        self.assertEqual(len(found),2)
        self.assertEqual(text[found[1][0]:],'(2026. 00. 00.)')
        self.assertEqual(occurrences(text,''),[])

    def test_header_title_and_bare_number_suggestions(self):
        text='업무연락\n수신: 기초센터장\n제목: 새 업무 안내\n2026-000\n(2026. 00. 00.)'
        result=library.suggestions(text)
        self.assertEqual(result['number'],'2026-000');self.assertEqual(result['date'],'(2026. 00. 00.)')
        self.assertEqual(result['title'],'새 업무 안내');self.assertEqual(result['receiver'],'기초센터장')
        self.assertEqual(library.number_pattern(result['number']).format(year=2026,number=12),'2026-012')
        self.assertEqual(library.suggestions('수신\n\n기초센터장\n\n제목\n\n새 제목')['title'],'새 제목')

    def test_original_hwp_is_picked_directly_without_zip(self):
        from template_manager import open_pick_template
        app=SimpleNamespace(root=object())
        with patch('template_manager.filedialog.askopenfilename',return_value='C:/forms/form.hwp') as ask,patch('template_manager.open_add_template') as opened:
            open_pick_template(app)
        self.assertEqual(ask.call_args.kwargs['filetypes'],[('한글 양식','*.hwp')])
        self.assertEqual(opened.call_args.kwargs['source'],'C:/forms/form.hwp')

    def test_cancel_file_pick_opens_no_registration_window(self):
        from template_manager import open_pick_template
        with patch('template_manager.filedialog.askopenfilename',return_value=''),patch('template_manager.open_add_template') as opened:
            open_pick_template(SimpleNamespace(root=object()))
        opened.assert_not_called()

    def test_form_dialog_exposes_duplicate_location_selection(self):
        from template_manager import open_add_template
        from tkinter import ttk
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=SimpleNamespace(root=root,config={'root':'C:/output'},data=Path('C:/unused'),status=Mock())
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        source=Path(temporary.name)/'test.hwp';source.write_bytes(b'owned fixture')
        text='제목 제목 입력\n수신 기초센터장\n서울센터 2026-000\n(2026. 00. 00.)\n본문 참조 (2026. 00. 00.)'
        with patch.object(library,'inspect',return_value=text):win=open_add_template(app,source=source)
        root.update()
        def walk(parent):
            for child in parent.winfo_children():
                yield child;yield from walk(child)
        combos=[w for w in walk(win) if isinstance(w,ttk.Combobox) and len(w.cget('values'))==2]
        self.assertEqual(len(combos),1)
        self.assertEqual(combos[0].current(),-1)
        self.assertIn('본문 참조',combos[0].cget('values')[1])
        combos[0].current(1)
        with patch.object(library,'add',return_value={'id':'user_test','label':'시험'}) as added:
            button=next(w for w in walk(win) if isinstance(w,ttk.Button) and w.cget('text')=='양식 추가');button.invoke()
        self.assertEqual(added.call_args.args[1]['_occurrences']['date'],1)


if __name__=='__main__':unittest.main()
