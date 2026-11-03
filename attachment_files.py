"""Accept completed files of any format; only native text documents are extracted."""
from pathlib import Path

TEXT_DOCUMENTS={'.pdf','.hwp','.hwpx'}
TEMP_SUFFIXES={'.crdownload','.part','.partial','.filepart','.download','.tmp','.temp'}
SYSTEM_NAMES={'desktop.ini','thumbs.db','.ds_store'}
FILE_TYPES=[('모든 첨부파일','*.*'),('PDF·한글·압축파일','*.pdf *.hwp *.hwpx *.zip'),('엑셀·표 파일','*.xls *.xlsx *.xlsm *.xlsb *.csv *.ods'),('워드·문서 파일','*.doc *.docx *.rtf *.txt')]


def eligible(path):
    path=Path(path)
    if path.name.startswith('~$') or path.suffix.lower() in TEMP_SUFFIXES or path.name.lower() in SYSTEM_NAMES:return False
    try:return path.is_file()
    except OSError:return False


def can_extract(path):
    return Path(path).suffix.lower() in TEXT_DOCUMENTS
