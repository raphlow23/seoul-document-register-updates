"""List completed documents and ZIP bundles; exclude images and other files."""
from pathlib import Path

TEXT_DOCUMENTS={'.pdf','.hwp','.hwpx'}
DOCUMENT_SUFFIXES=TEXT_DOCUMENTS|{'.zip','.xls','.xlsx','.xlsm','.xlsb','.csv','.tsv','.ods','.html','.htm','.mhtml','.mht','.doc','.docx','.docm','.rtf','.txt','.odt','.ppt','.pptx','.pptm','.odp','.dwg','.dxf'}
ZIP_DOCUMENTS=DOCUMENT_SUFFIXES-{'.zip','.txt'}
TEMP_SUFFIXES={'.crdownload','.part','.partial','.filepart','.download','.tmp','.temp'}
SYSTEM_NAMES={'desktop.ini','thumbs.db','.ds_store'}
FILE_TYPES=[('문서·도면·압축 첨부파일',' '.join('*'+s for s in sorted(DOCUMENT_SUFFIXES))),('PDF·한글·압축파일','*.pdf *.hwp *.hwpx *.zip'),('엑셀·표 파일','*.xls *.xlsx *.xlsm *.xlsb *.csv *.ods'),('HTML 문서','*.html *.htm *.mhtml *.mht'),('도면 파일','*.dwg *.dxf'),('워드·문서 파일','*.doc *.docx *.rtf *.txt')]


def eligible(path):
    path=Path(path)
    if path.suffix.lower() not in DOCUMENT_SUFFIXES:return False
    if path.name.startswith('~$') or path.suffix.lower() in TEMP_SUFFIXES or path.name.lower() in SYSTEM_NAMES:return False
    try:return path.is_file()
    except OSError:return False


def can_extract(path):
    return Path(path).suffix.lower() in TEXT_DOCUMENTS


def storage_files(job):
    result=[];seen=set()
    for file in [*job.get('files',[]),*job.get('archive_files',[])]:
        key=(file['path'],file['hash'])
        if key not in seen:result.append(file);seen.add(key)
    return result
