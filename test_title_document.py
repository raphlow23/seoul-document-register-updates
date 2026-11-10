from pathlib import Path
import io,tempfile,unittest,zipfile
from unittest.mock import patch
from pypdf import PdfWriter
import core
from title_document import prepare
from attachment_files import title_priority


class MailZipTitleTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.store=core.Store(self.base/'history.sqlite');self.addCleanup(self.store.db.close)
        writer=PdfWriter();writer.add_blank_page(width=300,height=400);out=io.BytesIO();writer.write(out);self.pdf=out.getvalue()

    def test_three_mail_attachments_choose_scanned_pdf_ocr_and_keep_all_files(self):
        path=self.base/'함께다운로드.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('1.붙임양식.hwpx',b'bad attachment');z.writestr('2.수신공문.pdf',self.pdf);z.writestr('3.명단.xlsx',b'office attachment')
        original=path.read_bytes()
        with patch('ocr.read_pdf',return_value=('제목 시험 공문\n2. OCR 본문을 정상적으로 읽었습니다.',{'ocr_pages':1,'confidence':99,'needs_review':False})) as ocr:
            job,_=core.ingest(path,self.store);files=sorted(job['files'],key=title_priority)
            self.assertEqual(Path(files[0]['path']).suffix,'.pdf')
            result=prepare(files[0],self.store,{})
        self.assertEqual(ocr.call_count,1);self.assertIn('OCR 본문',result['text']);self.assertIsNotNone(result['image']);result['image'].close()
        self.assertTrue(any(o['title']=='시험 공문' for o in result['options']));self.assertEqual(len(job['files']),3)
        self.assertEqual(path.read_bytes(),original)

    def test_bad_hwpx_remains_selectable_with_filename_and_warning(self):
        path=self.base/'broken.hwpx';path.write_bytes(b'not a zip');file={'path':str(path),'hash':core.digest(path)}
        result=prepare(file,self.store,{})
        self.assertEqual(result['text'],'');self.assertIn('다른 첨부파일',result['warning'])
        self.assertTrue(any(o['mode']=='filename' for o in result['options']))
        self.assertEqual(path.read_bytes(),b'not a zip')

    def test_actual_pdf_with_hwpx_extension_still_previews_and_ocr_reads_content(self):
        path=self.base/'잘못된확장자.hwpx';path.write_bytes(self.pdf);file={'path':str(path),'hash':core.digest(path)}
        with patch('ocr.read_pdf',return_value=('제목 실제 PDF\n2. 확장자가 달라도 본문을 정상적으로 읽습니다.',{})) as ocr:result=prepare(file,self.store,{})
        ocr.assert_called_once();self.assertIn('실제 PDF',result['text']);self.assertIsNotNone(result['image']);result['image'].close()
        self.assertFalse(result['warning']);self.assertEqual(path.read_bytes(),self.pdf)
