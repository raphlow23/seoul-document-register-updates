"""Build a reproducible OCR component and compile its file hashes into the application."""
import hashlib
import json
from pathlib import Path
import zipfile


def prepare(base):
    base=Path(base);root=base/'ocr-runtime'
    paths=sorted([p for p in root.iterdir() if p.suffix.lower() in ('.exe','.dll')]+list((root/'tessdata').glob('*.traineddata')))
    files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    if not {'tesseract.exe','tessdata/kor.traineddata','tessdata/eng.traineddata','tessdata/osd.traineddata'}<=files.keys():raise ValueError('Incomplete native OCR runtime')
    identity=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    name='OCRRuntime-'+identity[:16]+'.zip'
    (base/'ocr_runtime_info.py').write_text('RUNTIME_ID = '+repr(identity)+'\nCOMPONENT_NAME = '+repr(name)+'\nFILES = '+repr(files)+'\n',encoding='utf-8')
    out=base/'public-build';out.mkdir(exist_ok=True);archive=out/name
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for relative in files:
            info=zipfile.ZipInfo(relative,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,(root/relative).read_bytes())
    return archive


if __name__=='__main__':print(prepare(Path(__file__).resolve().parent))
