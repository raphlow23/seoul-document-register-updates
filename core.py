"""Local document extraction, routing and recoverable Sheets registration."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import uuid
import zipfile
import zlib
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import date

# Account/workplace values are installed locally, never embedded in public update assets.
ROOT = ""
SHEET_ID = ""
TAB = "2026년 문서접수대장"
RECEIPT_TABS={"공문":TAB,"업무연락":"2026년(업무연락)수신","처리지침":"2026년(처리지침)수신"}
CATEGORIES = {
    "기초센터 공문": ["00.기초센터 공문"],
    "서울시청 공문": ["0.시청공문"],
    "중앙센터 공문": ["0.중앙센터 공문"],
    "완화공문": ["3.완화신청"],
    "중앙센터 편의시설 지침": ["0.중앙센터 공문", r"5.보건복지부 및 중앙처리지침\1. 중앙지침(처리지침, 업무연락 등)"],
    "중앙센터 전달 보건복지부 지침": ["0.중앙센터 공문", r"5.보건복지부 및 중앙처리지침\0. 보건복지부 지침"],
}
EXTENSIONS = {".pdf", ".hwp", ".hwpx", ".zip"}


def digest(path):
    from fs_paths import io_path
    h = hashlib.sha256()
    with open(io_path(path), "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def clean_hwp_text(payload):
    # HWP 5: extended inline controls occupy eight UTF-16 code units.
    units = struct.unpack("<" + "H" * (len(payload) // 2), payload[:len(payload)//2*2])
    out, i = [], 0
    extended = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
    inline = {4, 5, 6, 7, 8, 9, 19, 20}
    while i < len(units):
        c = units[i]
        if c in extended | inline:
            if c == 9:
                out.append(" ")
            i += 8
        else:
            if c in (10, 13):
                out.append("\n")
            elif c >= 32:
                out.append(chr(c))
            i += 1
    return "".join(out)


def extract(path, with_meta=False, progress=None):
    from fs_paths import io_path
    path = io_path(path)
    metadata = {}
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("100MB를 넘는 파일은 직접 확인해 주세요.")
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("암호가 있는 PDF입니다.")
        from ocr import read_pdf
        text, metadata = read_pdf(path, progress=progress)
    elif ext == ".hwpx":
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n))
            if not names:
                raise ValueError("HWPX 본문을 찾지 못했습니다.")
            if sum(z.getinfo(n).file_size for n in names) > 100 * 1024 * 1024:
                raise ValueError("HWPX 본문이 너무 큽니다.")
            paragraphs = []
            for name in names:
                root = ET.fromstring(z.read(name))
                for p in root.iter():
                    if p.tag.rsplit("}", 1)[-1] == "p":
                        paragraphs.append("".join(t.text or "" for t in p.iter() if t.tag.rsplit("}", 1)[-1] == "t"))
            text = "\n".join(paragraphs)
    elif ext == ".hwp":
        import olefile
        with olefile.OleFileIO(path) as ole:
            header = ole.openstream("FileHeader").read()
            if len(header) < 40 or not header.startswith(b"HWP Document File"):
                raise ValueError("지원하는 HWP 5 문서가 아닙니다.")
            flags = struct.unpack_from("<I", header, 36)[0]
            if flags & (2 | 4):
                raise ValueError("암호 또는 배포용 HWP입니다. PDF로 저장해 추가해 주세요.")
            names = sorted(n for n in ole.listdir() if len(n) == 2 and n[0] == "BodyText" and n[1].startswith("Section"))
            paragraphs = []
            for name in names:
                data = ole.openstream(name).read()
                if flags & 1:
                    dec = zlib.decompressobj(-15)
                    data = dec.decompress(data, 100 * 1024 * 1024 + 1)
                    if dec.unconsumed_tail or len(data) > 100 * 1024 * 1024:
                        raise ValueError("HWP 본문이 너무 큽니다.")
                pos = 0
                while pos + 4 <= len(data):
                    record = struct.unpack_from("<I", data, pos)[0]
                    tag, size = record & 1023, record >> 20
                    pos += 4
                    if size == 4095:
                        if pos + 4 > len(data):
                            raise ValueError("손상된 HWP 레코드입니다.")
                        size = struct.unpack_from("<I", data, pos)[0]
                        pos += 4
                    if pos + size > len(data):
                        raise ValueError("손상된 HWP 본문입니다.")
                    if tag == 67:
                        paragraphs.append(clean_hwp_text(data[pos:pos + size]))
                    pos += size
            text = "\n".join(paragraphs)
    else:
        raise ValueError("HWP, HWPX, PDF 파일을 선택해 주세요.")
    text = text.replace("\x00", "").strip()
    if len(re.sub(r"\s", "", text)) < 15:
        raise ValueError("OCR에서도 본문을 읽지 못했습니다. 스캔 화질을 확인하거나 제목·분류를 직접 입력해 주세요.")
    result = text[:500000]
    return (result, metadata) if with_meta else result


def classify(text, filename=""):
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in text.splitlines() if x.strip()]
    title = ""
    for i, line in enumerate(lines):
        m = re.match(r"^제\s*목\s*[:：]?\s*(.+)$", line)
        if m:
            title = m.group(1).strip()
            break
        if re.fullmatch(r"제\s*목\s*[:：]?", line) and i + 1 < len(lines):
            title = lines[i+1]
            break
    # A reference to an institution in the body is not enough to identify its sender.
    central = any(re.fullmatch(r"[（(]?(?:한국지체장애인협회\s*)?(?:중앙장애인편의증진기술지원센터|장애인편의증진기술지원중앙센터|중앙센터)(?:장)?[）)]?(?:\s*\(.*\))?", x.replace(" ", "")) for x in lines)
    city = any(re.fullmatch(r"서울특별시장(?:\s*\(.*\))?", x.replace(" ", "")) for x in lines)
    category = ""
    if central and not city:
        category = "중앙센터 공문"
        if re.search(r"처리\s*지침|업무\s*연락|편의시설.{0,12}지침", text):
            category = "중앙센터 편의시설 지침"
            if "보건복지부" in text:
                category = "중앙센터 전달 보건복지부 지침"
    elif city and not central:
        category = "서울시청 공문"
    issuer = "서울시청" if city and not central else "중앙센터" if central and not city else ""
    # Ministry and guideline references need review: a quoted reference is not proof of an enclosure.
    ready = bool(title and category and category in ("서울시청 공문", "중앙센터 공문"))
    reason = "발신 기관·제목 확인" if ready else "지침·첨부 관계 확인 필요" if category else "발신 기관 또는 공문 제목 확인 필요"
    from title_tools import header_title
    title = header_title(text) or title
    return {"title": title or Path(filename).stem, "category": category, "issuer": issuer,
            "date": date.today().isoformat(), "recipient": "", "ready": ready, "reason": reason}


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (hash TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.commit()

    def all(self):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM jobs ORDER BY rowid DESC")]

    def get(self, hash_):
        row = self.db.execute("SELECT payload FROM jobs WHERE hash=?", (hash_,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_many(self,jobs):
        try:
            self.db.executemany("INSERT INTO jobs VALUES (?,?) ON CONFLICT(hash) DO UPDATE SET payload=excluded.payload",[(job['hash'],json.dumps(job,ensure_ascii=False)) for job in jobs])
            self.db.commit()
        except Exception:
            self.db.rollback();raise

    def put(self, job):
        self.db.execute("INSERT INTO jobs VALUES (?,?) ON CONFLICT(hash) DO UPDATE SET payload=excluded.payload",
                        (job["hash"], json.dumps(job, ensure_ascii=False)))
        self.db.commit()


def ingest(path, store, progress=None):
    path = Path(path)
    from attachment_files import eligible
    if not eligible(path):raise ValueError('문서·ZIP 첨부파일만 추가할 수 있습니다. 이미지와 기타 파일은 제외합니다.')
    hash_ = digest(path)
    old = store.get(hash_)
    if old:
        if old.get("archive_alias"):old=store.get(old["parent"]) or old
        changed=False
        for file in old.get("files",[]):
            if file.get("hash")==hash_ and not Path(file["path"]).is_file():
                file["path"]=str(path);changed=True
        if old.get("source_archive") and path.suffix.lower()==".zip" and not Path(old["source_archive"]).is_file():
            old["source_archive"]=str(path);changed=True
        if changed:store.put(old)
        return old, False
    if path.suffix.lower()==".zip":
        from zip_intake import ingest_zip
        return ingest_zip(path,store,hash_,progress)
    text, metadata, info = inspect_document(path,progress)
    job = {"hash": hash_, "id": uuid.uuid4().hex, "files": [{"path": str(path), "hash": hash_}],
           "text": text, "text_file_hash":hash_, "ocr": metadata, "state": "확인 필요", "number": "", "destinations": [], "receipt_type":"공문", **info}
    store.put(job)
    return job, True



def inspect_document(path,progress=None):
    path=Path(path)
    try:
        from attachment_files import can_extract
        if can_extract(path):text, metadata = extract(path, with_meta=True, progress=progress)
        else:text, metadata='',{}
        info = classify(text, path.name)
        if not can_extract(path):info.update(ready=False,reason='첨부 보관용 파일 · 제목은 파일명에서 선택하거나 직접 입력하세요.')
        if metadata.get("ocr_pages"):
            info["reason"] = f"한국어 OCR {metadata['ocr_pages']}쪽 · 평균 인식 신뢰도 {metadata['confidence']}점 · " + info["reason"]
            # A high average is not proof of correct names/numbers; extraction rules must also match.
            if metadata["needs_review"]:
                info["ready"] = False
                info["reason"] += " · 흐린 글자 확인 필요"
    except Exception as e:
        metadata = {}
        text, info = "", classify("", path.name)
        info["reason"] = str(e)
    return text,metadata,info


def attachment_aliases(job,store,state):
    result=[]
    for file in job.get("files",[]):
        if file["hash"]==job["hash"]:continue
        old=store.get(file["hash"])
        if old and old.get("parent")!=job["hash"] and not (old.get('id') and old.get("id")==job.get("id")):continue
        text=job.get("text","") if file["hash"]==job.get("text_file_hash") else ""
        result.append({**job,"hash":file["hash"],"files":[file],"parent":job["hash"],"state":state,"text":text})
    return result


def receipt_markers(job):
    if job.get("receipt_registration_markers") and (job.get('sheet_attempted') or job.get('receipt_done')):return list(job["receipt_registration_markers"])
    files=job.get("receipt_marker_files") or job["files"]
    if job.get("sheet_attempted") or job.get("receipt_done"):
        return ["SDR:"+file["hash"] for file in files]
    identity={"files":sorted({file["hash"] for file in files}),"archives":sorted({file["hash"] for file in job.get("archive_files",[])})}
    marker="SDR:group:"+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    job["receipt_registration_markers"]=[marker]
    return [marker]


def copy_files(job, root, subfolders=None,on_progress=None):
    from attachment_files import storage_files
    from fs_paths import io_path
    root = Path(root)
    if not root.is_dir():
        raise ValueError("공유폴더에 접근할 수 없습니다. 탐색기에서 연결을 확인해 주세요.")
    if subfolders is None and job["category"] not in CATEGORIES:
        raise ValueError("분류를 선택해 주세요.")
    planned = []
    reserved = {}
    for sub in subfolders if subfolders is not None else CATEGORIES[job["category"]]:
        folder = root / sub
        if not folder.is_dir():
            raise ValueError(f"저장 폴더가 없습니다: {folder}")
        for f in storage_files(job):
            source = Path(f["path"])
            if not io_path(source).is_file() or digest(source) != f["hash"]:
                raise ValueError(f"원본이 없거나 내용이 바뀌었습니다: {source.name}")
            target = folder / source.name
            key=str(target).casefold()
            if (io_path(target).exists() and digest(target) != f["hash"]) or (key in reserved and reserved[key]!=f["hash"]):
                target = folder / f"{source.stem}_{f['hash'][:12]}{source.suffix}"
            key=str(target).casefold()
            if key in reserved and reserved[key]!=f["hash"]:
                raise ValueError(f"첨부파일 이름이 충돌합니다: {target.name}")
            reserved[key]=f["hash"]
            planned.append((source, target, f["hash"]))
    results = []
    for source, target, hash_ in planned:
        if io_path(target).exists():
            if digest(target) != hash_:
                raise ValueError(f"같은 이름의 다른 파일이 있습니다: {target}")
        else:
            # Exclusive creation never overwrites an existing file, even after a race.
            created = False
            try:
                with io_path(target).open("xb") as out:
                    created = True
                    with io_path(source).open("rb") as src:
                        shutil.copyfileobj(src, out)
                        out.flush()
                        os.fsync(out.fileno())
            except FileExistsError:
                if digest(target) != hash_:
                    raise ValueError(f"저장 중 이름 충돌: {target}")
            except Exception:
                # Keep a failed partial copy distinguishable; original downloads stay intact.
                if created and io_path(target).exists():
                    try:
                        incomplete=target.with_name(target.name + ".incomplete-" + uuid.uuid4().hex[:8])
                        io_path(target).rename(io_path(incomplete))
                        from file_rollback import track_file
                        track_file(job,incomplete,digest(incomplete),'receipt')
                        if on_progress:on_progress()
                    except OSError:
                        pass
                raise
            if created:
                from file_rollback import track_file
                track_file(job,target,hash_,'receipt')
                if on_progress:on_progress()
        if digest(target) != hash_:
            raise ValueError(f"복사 검증 실패: {target}")
        results.append(str(target))
    return results


def value(text):
    return {"userEnteredValue": {"stringValue": str(text)}}


def registration_requests(sheet_id, row, job, previous, marker, candidate=None):
    if row < 6:
        raise ValueError("접수대장 첫 데이터 행을 먼저 확인해 주세요.")
    serial = (date.fromisoformat(job["date"]) - date(1899, 12, 30)).days
    cells = [
        {"userEnteredValue": {"formulaValue": f"=A{row-1}+1"}},
        {"userEnteredValue": {"numberValue": serial}},
        value(previous[2] if len(previous) > 2 else "서울센터2026-"),
        {"userEnteredValue": {"formulaValue": f"=D{row-1}+1"}},
        value(job["issuer"]), value(job["title"]), value(job["recipient"]), value("Email"),
    ]
    cells[5]["note"] = marker
    src = {"sheetId": sheet_id, "startRowIndex": row-2, "endRowIndex": row-1, "startColumnIndex": 0, "endColumnIndex": 8}
    dst = {**src, "startRowIndex": row-1, "endRowIndex": row}
    requests = [
        {"copyPaste": {"source": src, "destination": dst, "pasteType": "PASTE_FORMAT"}},
        {"copyPaste": {"source": src, "destination": dst, "pasteType": "PASTE_DATA_VALIDATION"}},
    ]
    candidate = candidate or []
    if any(str(x).strip() for x in candidate):
        requests = []
    for col, cell in enumerate(cells):
        if col in (0, 1, 2, 3) and col < len(candidate) and str(candidate[col]).strip():
            continue
        requests.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row-1, "columnIndex": col},
                         "rows": [{"values": [cell]}], "fields": "userEnteredValue"}})
    requests.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row-1, "columnIndex": 5},
                         "rows": [{"values": [{"note": marker}]}], "fields": "note"}})
    return requests


class Sheets:
    last_column = "H"
    title_column = 5
    note_column = "F"
    header_word = "문"
    marker_prefix = "SDR:"
    header_row = 2
    notes_start = 5
    def validate_header(self,rows):
        if self.marker_prefix!='SDR:':
            header=rows[self.header_row] if len(rows)>self.header_row else []
            if len(header)<=self.title_column or '제' not in header[self.title_column] or self.header_word not in header[2]:
                raise ValueError(f'{self.tab}의 대장 열 구성을 확인하지 못했습니다. 등록하지 않았습니다.')
            return
        matches=[]
        for i,row in enumerate(rows[:50]):
            cells=[re.sub(r'\s+','',str(v)) for v in row]+['']*8
            if not re.fullmatch(r'(?:(?:문서|공문|수신|접수)?제목|건명|문서명)(?:\([^)]*\))?',cells[5]):continue
            # A:D may be merged vertically or C:D may share one document-number heading.
            columns=[''.join(re.sub(r'\s+','',str(r[c])) for r in rows[max(0,i-2):i+1] if len(r)>c) for c in range(8)]
            if '연번' not in columns[0] or not any(w in columns[1] for w in ('일자','일','날짜')):continue
            if not any(w in columns[2]+columns[3] for w in ('문서','업무연락','처리지침','접수번호','번호')):continue
            if not any(w in columns[4] for w in ('발신','발송','보낸')):continue
            matches.append(i)
        if len(matches)!=1:raise ValueError(f'{self.tab}의 수신대장 머리글을 확인하지 못했습니다. A열 연번·B열 일자·C:D열 문서/업무연락/처리지침 번호·E열 발신처·F열 제목을 확인하세요. 등록하지 않았습니다.')
        self.header_row=matches[0];self.notes_start=self.header_row+2
    def __init__(self, config):
        self.browser=None
        if config.get("auth_mode")=="browser":
            self.tab=config.get("tab",TAB)
            from browser_sheets import BrowserSession
            self.browser=BrowserSession(config,self)
            return
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        mode = config.get("auth_mode", "service" if config.get("key_path") else "oauth")
        if mode == "oauth":
            from google_login import credentials
            creds = credentials(config)
        else:
            key = Path(config.get("key_path", ""))
            if not key.is_file():
                raise ValueError("설정에서 Google 서비스 계정 JSON 키를 선택해 주세요.")
            info = json.loads(key.read_text(encoding="utf-8-sig"))
            if info.get("type") != "service_account" or info.get("token_uri") != "https://oauth2.googleapis.com/token":
                raise ValueError("올바른 Google 서비스 계정 JSON 키가 아닙니다.")
            creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.session = AuthorizedSession(creds)
        sid = config.get("spreadsheet_id", SHEET_ID)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            raise ValueError("구글 시트 ID가 올바르지 않습니다.")
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
        self.tab = config.get("tab", TAB)

    def request(self, method, suffix="", **kwargs):
        if getattr(self,"browser",None):return self.browser.request(method,suffix,**kwargs)
        r = self.session.request(method, self.base + suffix, timeout=30, **kwargs)
        if r.status_code >= 400:
            if method == "POST" and r.status_code in (400, 401, 403, 404, 409, 422, 429):
                raise RejectedWrite(f"구글 시트가 등록 요청을 거절했습니다({r.status_code}). API·편집 권한·시트 설정을 확인한 뒤 다시 시도해 주세요.")
            if r.status_code in (401, 403):
                raise ValueError("구글 시트 권한 오류입니다. Sheets API 사용 설정과 연결한 계정의 시트 편집 권한을 확인해 주세요.")
            raise ValueError(f"구글 시트 요청 실패({r.status_code}). 연결 상태와 시트 설정을 확인해 주세요.")
        return r.json()

    def snapshot(self):
        if getattr(self,"browser",None):return self.browser.snapshot()
        q = "'" + self.tab.replace("'", "''") + "'"
        meta = self.request("GET", params={"fields": "sheets.properties"})
        prop = next((x["properties"] for x in meta["sheets"] if x["properties"]["title"] == self.tab), None)
        if prop is None:
            raise ValueError("설정한 접수대장 탭을 찾지 못했습니다.")
        n = prop["gridProperties"]["rowCount"]
        if n > 20000:
            raise ValueError("시트가 너무 큽니다. 접수대장 설정을 확인해 주세요.")
        rows = self.request("GET", "/values/" + __import__("urllib.parse", fromlist=["quote"]).quote(f"{q}!A1:{self.last_column}{n}", safe=""),
                            params={"valueRenderOption": "FORMATTED_VALUE"}).get("values", [])
        if hasattr(self,'validate_header'):self.validate_header(rows)
        # F notes are durable transaction markers for uncertain HTTP outcomes / lost local state.
        notes = self.request("GET", params={"ranges": f"{q}!{self.note_column}{self.notes_start}:{self.note_column}{n}", "fields": "sheets.data(startRow,rowData.values.note)"})
        found = {}
        self.notes = {}
        for sheet in notes.get("sheets", []):
            for block in sheet.get("data", []):
                for i, r in enumerate(block.get("rowData", [])):
                    for c in r.get("values", []):
                        self.notes[block.get("startRow", 0) + i + 1] = c.get("note", "")
                        for mark in c.get("note", "").splitlines():
                            if mark.startswith(self.marker_prefix):
                                found[mark] = block.get("startRow", 0) + i + 1
        header=rows[self.header_row] if len(rows)>self.header_row else []
        if not hasattr(self,'validate_header') and (len(header) <= self.title_column or "제" not in header[self.title_column] or self.header_word not in header[2]):
            raise ValueError("접수대장 열 구성이 예상과 다릅니다. 자동 등록을 중단했습니다.")
        return prop, rows, found

    def __del__(self):
        browser=getattr(self,"browser",None)
        if browser:
            try:browser.close()
            except Exception:pass

    def register(self, job, store):
        markers=receipt_markers(job)
        if getattr(self,"browser",None):
            self.browser.snapshot()
            self.browser.recover(markers)
        prop, rows, found = self.snapshot()
        existing = {found[m] for m in markers if m in found}
        if len(existing) > 1:
            raise ValueError("묶음 파일이 서로 다른 등록 행에 존재합니다. 확인해 주세요.")
        if existing:
            row = existing.pop()
            if any(m not in found for m in markers):
                raise ValueError("묶음 중 일부만 이미 등록되어 있습니다. 기존 등록을 확인해 주세요.")
        else:
            if job.get("sheet_attempted"):
                raise ValueError("이전 등록 결과가 불확실합니다. 시트에서 등록 여부를 확인한 뒤 설정을 점검해 주세요.")
            populated = [i + 1 for i, r in enumerate(rows) if i > self.header_row and len(r) > 5 and str(r[5]).strip() and str(r[0]).isdigit() and str(r[3]).isdigit()]
            if not populated:
                raise ValueError("기존 접수번호 기준 행이 없습니다.")
            row = max(populated) + 1
            if row > prop["gridProperties"]["rowCount"]:
                raise ValueError("시트에 빈 행을 추가한 뒤 다시 실행해 주세요.")
            # Prepared rows may contain A/C/D numbering and today's date already.
            candidate = rows[row-1] if row <= len(rows) else []
            prev = rows[row-2]
            if len(prev) < 4 or not re.fullmatch(r"\d+", str(prev[0])) or not re.fullmatch(r"\d+", str(prev[3])):
                raise ValueError("바로 앞 행의 연번·접수번호가 숫자가 아닙니다.")
            if any(str(x).strip() for x in candidate[4:8]):
                raise ValueError(f"다음 행({row}행)에 기존 발신처·제목·수신자·방법이 있습니다.")
            expected = {0: str(int(prev[0]) + 1), 2: str(prev[2]), 3: str(int(prev[3]) + 1)}
            for col, number in expected.items():
                if col < len(candidate) and str(candidate[col]).strip():
                    actual = str(candidate[col])
                    if (col in (0, 3) and (not actual.isdigit() or int(actual) != int(number))) or (col == 2 and actual != number):
                        raise ValueError(f"다음 행({row}행)의 미리 입력된 번호가 앞 행과 맞지 않습니다.")
            if len(candidate) > 1 and str(candidate[1]).strip() and candidate[1] != job["date"]:
                raise ValueError(f"다음 행({row}행)의 미리 입력된 날짜가 접수일과 다릅니다. 시트 날짜를 확인해 주세요.")
            # Persist the attempted transaction before POST; never blindly repeat a write.
            job["sheet_attempted"] = True
            store.put(job)
            note = getattr(self, "notes", {}).get(row, "")
            marker = (note + "\n" if note else "") + "\n".join(markers)
            reqs = registration_requests(prop["sheetId"], row, job, prev, marker, candidate)
            try:
                self.request("POST", ":batchUpdate", json={"requests": reqs})
            except RejectedWrite:
                job["sheet_attempted"] = False
                store.put(job)
                raise
        q = "'" + self.tab.replace("'", "''") + "'"
        from urllib.parse import quote
        result = self.request("GET", "/values/" + quote(f"{q}!D{row}:F{row}", safe=""), params={"valueRenderOption": "FORMATTED_VALUE"}).get("values", [])
        if not result or not result[0] or not re.fullmatch(r"\d+", str(result[0][0])):
            raise ValueError("D열 번호를 확인하지 못했습니다. 시트에서 확인 후 다시 시도해 주세요.")
        if len(result[0]) < 3 or result[0][2] != job["title"]:
            raise ValueError("등록 행의 제목이 다릅니다. 시트를 확인해 주세요.")
        return row, str(result[0][0])


class RejectedWrite(ValueError):
    pass


def apply_title_choice(data, store):
    jobs = [store.get(hash_) for hash_ in data["jobs"]]
    if not jobs or any(j is None or j["state"] in ("완료", "묶음 처리 완료", "묶음에 포함") or j.get("sheet_attempted") or j.get("files_started") for j in jobs):
        raise ValueError("선택한 공문이 이미 처리되었거나 변경되었습니다. 목록을 다시 확인해 주세요.")
    primary = next((j for j in jobs if j["hash"] == data["primary"]), None)
    if primary is None or not data["title"].strip() or len(data["title"]) > 500:
        raise ValueError("대표 공문과 제목을 확인해 주세요.")
    files = []
    for job in jobs:
        for file in job["files"]:
            if file["hash"] not in [f["hash"] for f in files]: files.append(file)
    if data["source"]["file_hash"] not in [f["hash"] for f in files]:
        raise ValueError("제목 출처가 선택한 첨부파일에 포함되지 않습니다.")
    primary.update({k:v for k,v in data.get("fields", {}).items() if k in ("category", "date", "recipient", "issuer", "receipt_type")})
    primary.update(title=data["title"].strip(), title_source=data["source"], files=files, ready=False,
                   state="확인 필요", reason=f"제목 선택 완료 · 첨부 {len(files)}개를 한 공문으로 등록")
    archives={file['hash']:file for job in jobs for file in job.get('archive_files',[])}
    if archives:primary['archive_files']=list(archives.values())
    grouped = []
    for job in jobs:
        if job["hash"] != primary["hash"]:
            job.update(state="묶음에 포함", parent=primary["hash"])
            grouped.append(job)
    for job in grouped: store.put(job)
    store.put(primary)
    return primary, grouped


@contextmanager
def registration_lock(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("공유폴더에 접근할 수 없습니다.")
    lock = root / ".수신등록-처리중"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("다른 등록 작업이 진행 중이거나 이전 실행이 중단되었습니다. 사용설명서의 잠금 복구 항목을 확인해 주세요.")
    try:
        (lock / "owner.txt").write_text(f"PC: {os.environ.get('COMPUTERNAME', '')}\nPID: {os.getpid()}\n", encoding="utf-8")
        yield
    finally:
        try:
            (lock / "owner.txt").unlink(missing_ok=True)
            lock.rmdir()
        except OSError:
            pass


def process(job, config, store, sheets_factory=Sheets, outgoing_factory=None, stamp_factory=None):
    with registration_lock(config["root"]):
        result=_process(job, config, store, sheets_factory, outgoing_factory, stamp_factory)
        from program_storage import cleanup_rollbacks
        try:cleanup_rollbacks(result,config,store)
        except (OSError,ValueError) as error:
            result['cleanup_warning']=str(error);store.put(result)
        return result


def _process(job, config, store, sheets_factory=Sheets, outgoing_factory=None, stamp_factory=None):
    from outgoing import validate as validate_outgoing, finish as finish_outgoing
    if job.get('receipt_done') and job.get('files_rolled_back'):
        from folder_naming import copy_to_subfolders
        job['destinations']=copy_to_subfolders(job,config['root'],on_progress=lambda:store.put(job))
        job['files_rolled_back']=False
        if not config.get('_register_outgoing_after_restore'):
            out=job.get('outgoing',{})
            job['state']='수신 완료·발신 등록 대기' if out.get('enabled') and not (out.get('complete') and out.get('stamp',{}).get('complete')) else '완료'
            job['reason']='파일만 다시 저장 완료 · 기존 구글시트 등록·번호 유지'
            store.put_many([job,*attachment_aliases(job,store,'묶음 처리 완료')])
            return job
        store.put(job)
    out=validate_outgoing(job,config)
    if job["state"] == "완료" or job.get("receipt_done"):
        job["receipt_done"]=True
        store.put(job)
        finish_outgoing(job,config,store,outgoing_factory,stamp_factory)
        job["state"]="완료"
        job["reason"]="수신·발신등록 완료" if out["enabled"] else "저장·수신등록 완료"
        store.put(job)
        store.put_many(attachment_aliases(job,store,'묶음 처리 완료'))
        return job
    receipt_type=job.get("receipt_type") or "공문"
    if receipt_type not in RECEIPT_TABS: raise ValueError("수신 등록 유형을 선택해 주세요.")
    receipt_config={**config,"tab":RECEIPT_TABS[receipt_type]}
    if not job["title"].strip() or not job["recipient"].strip():
        raise ValueError("제목과 수신자(담당자)를 입력해 주세요.")
    date.fromisoformat(job["date"])
    if job["category"] not in CATEGORIES:
        raise ValueError("분류를 선택해 주세요.")
    if not job.get("issuer"):
        job["issuer"] = (job.get('routing',{}).get('relief',{}).get('district') or '서울시청') if job['category']=='완화공문' else "서울시청" if job["category"] == "서울시청 공문" else "기초센터" if job["category"] == "기초센터 공문" else "중앙센터"
    if job.get("routing"):
        from folder_naming import plan_folders, copy_to_subfolders
        plan_folders(job, config["root"])
        store.put(job)
        job["destinations"] = copy_to_subfolders(job, config["root"],on_progress=lambda:store.put(job))
        job["state"] = "저장 완료·등록 대기"
        store.put(job)
        row, number = sheets_factory(receipt_config).register(job, store)
    else:
        job["destinations"] = copy_files(job, config["root"],on_progress=lambda:store.put(job))
        job["state"] = "저장 완료·등록 대기"
        store.put(job)
        row, number = sheets_factory(receipt_config).register(job, store)
    job["receipt_type"]=receipt_type
    job['files_rolled_back']=False
    job["receipt_tab"]=RECEIPT_TABS[receipt_type]
    job.update(state="수신 완료·발신 등록 대기" if out["enabled"] else "완료", row=row, number=number, receipt_done=True, reason="저장·수신등록 완료")
    store.put(job)
    finish_outgoing(job,config,store,outgoing_factory,stamp_factory)
    job.update(state="완료",reason="수신·발신등록 완료" if out["enabled"] else "저장·수신등록 완료")
    store.put(job)
    # Each bundled attachment is also recorded so downloading it separately is not a new job.
    store.put_many(attachment_aliases(job,store,'묶음 처리 완료'))
    return job
