"""Deterministic title suggestions from OCR, with source labels for human selection."""
from pathlib import Path
import re

TERMS = ("사업시행계획인가", "주택재개발정비사업", "재정비촉진구역", "도시정비형", "가로주택정비사업",
         "편의증진기술위원회", "편의시설", "검토 결과", "장애인등편의법", "서울특별시", "서대문구")


def normalize(text):
    for word in TERMS:
        text = re.sub(r"\s*".join(re.escape(c) for c in word.replace(" ", "")), word, text)
    return re.sub(r"[ \t]+", " ", text).strip()


def header_title(text):
    lines = [x.strip() for x in normalize(text).splitlines() if x.strip()]
    for i, line in enumerate(lines):
        match = re.match(r"^제\s*목\s*[:：]?\s*(.*)$", line)
        if not match:
            continue
        parts = [match.group(1)] if match.group(1) else []
        for following in lines[i+1:i+4]:
            if re.search(r"(?:합니다|입니다|바랍니다|드립니다|주십시오|하오니)[.!]?$", following):
                break
            if re.match(r"^(?:\d+[.)]|우리|귀\s|붙임|담당|수\s*신|발\s*신|시\s*행|접\s*수|□|[○ㅇ])", following):
                break
            if len(following) > 65 and parts:
                break
            if parts and (re.search(r"[。.]$", parts[-1]) or len(following) > 30):
                break
            parts.append(following)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()[:500]
    return ""


def agency_key(text):
    for line in text.splitlines()[:20]:
        compact = re.sub(r"\s+", "", line)
        if re.fullmatch(r"[가-힣]{2,12}(?:구|시|센터)(?:장)?", compact):
            return compact.rstrip("장")
    compact = re.sub(r"\s+", "", text)
    if "중앙장애인편의증진기술지원센터" in compact:
        return "중앙센터"
    return ""


def suggestions(text, filename="", preferred="header"):
    text = normalize(text)
    header = header_title(text)
    options = []
    def add(mode, label, title):
        title = re.sub(r"\s+", " ", title).strip()
        if title and not any(re.sub(r"\s", "", o["title"]) == re.sub(r"\s", "", title) for o in options):
            options.append({"mode": mode, "label": label, "title": title[:500]})
    add("header", "문서의 ‘제목’란", header)
    flat = re.sub(r"\s+", " ", text)
    project = re.search(r"추진\s*중인\s+(.{3,130}?사업)\s*(?:과\s*관련|관련|[,，])", flat)
    if not project:
        project = re.search(r"([가-힣0-9]+재정비촉진구역\s*.{0,40}?사업)\s*(?:과\s*관련|[,，])", flat)
    address = re.search(r"위\s*치\s*[:：]\s*([^\n(（]{5,100})", text)
    location = address.group(1).strip() if address else ""
    location = re.sub(r"^서울특별시\s*", "", location)
    if not location:
        address = re.search(r"([가-힣]{2,8}구\s+[가-힣0-9]{2,12}동\s+\d+(?:-\d+)?\s*(?:번지)?\s*(?:일대|일원))", flat)
        location = address.group(1) if address else ""
    if project and header:
        action = re.sub(r"\([^)]*\)|（[^）]*）", "", header).strip()
        name = project.group(1).strip()
        assembled = name + (" " + action if name not in action else "")
        if location:
            assembled += f"({location})"
        add("project", "본문 사업명 + 제목의 협의 내용 + 소재지", assembled)
    if header and location and location not in header:
        add("header_address", "제목 + 소재지", header + f"({location})")
    add("filename", "파일 이름", Path(filename).stem)
    return sorted(options, key=lambda o: 0 if o["mode"] == preferred else 1)


def pdf_preview(path, page=0):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(path)) as document:
        if not 0 <= page < len(document):
            raise ValueError("해당 PDF 페이지가 없습니다.")
        pdfpage = document[page]
        try:
            width, height = pdfpage.get_size()
            scale = min(1800/max(width, 1), (8_000_000/max(1, width*height))**0.5)
            bitmap = pdfpage.render(scale=scale)
            try:
                return bitmap.to_pil().convert("RGB").copy(), len(document)
            finally:
                bitmap.close()
        finally:
            pdfpage.close()
