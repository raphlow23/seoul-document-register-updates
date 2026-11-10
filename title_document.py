"""Read one extracted attachment; a bad attachment never blocks title selection."""
from pathlib import Path
import zipfile
from attachment_files import can_extract,document_kind
from title_tools import agency_key,suggestions,pdf_preview


def prepare(file,store,config,page=0,progress=None):
    from core import extract
    cached=store.get(file['hash'])
    text=cached.get('text','') if cached and cached.get('text_file_hash',cached['hash'])==file['hash'] else ''
    warnings=[];image=None;count=1
    try:
        if not text and can_extract(file['path']):text=extract(file['path'],progress=progress)
    except Exception as error:
        warnings.append('본문을 읽지 못했습니다: '+str(error))
    try:
        if can_extract(file['path']) and document_kind(file['path'])=='.pdf':image,count=pdf_preview(file['path'],page)
    except Exception as error:
        warnings.append('미리보기를 읽지 못했습니다: '+str(error))
    agency=agency_key(text);preferred=config.get('title_preferences',{}).get(agency,'header')
    return {'text':text,'image':image,'page_count':count,'agency':agency,'options':suggestions(text,file['path'],preferred),
            'warning':(' / '.join(warnings)+' · 다른 첨부파일을 고르거나 제목을 직접 입력하세요.') if warnings else ''}
