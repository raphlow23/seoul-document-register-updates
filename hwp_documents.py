"""Six supplied letter styles, exact template anchors and native Hancom HWP export."""
from copy import deepcopy
from datetime import date
import json
import io
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile
from functools import lru_cache

from document_draft import HP, _paragraph_texts, _set_paragraph


def template_root():
    import os
    private=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"SeoulDocumentRegister"/"hwp-templates"
    if (private/"catalog.json").is_file():return private
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "hwp-templates"


def catalog():
    path=template_root()/"catalog.json"
    if path.is_file():return json.loads(path.read_text(encoding="utf-8"))
    labels=("일반 25개구 기초센터 발신공문","중앙 회신 공문","시청 회신 공문","업무연락 공문","처리지침 공문","서울시청 건축협의 회신 공문")
    return [{"id":str(i+1),"label":label,"file":f"template{i+1}.hwp","family":"업무연락" if i==3 else "처리지침" if i==4 else "공문","folder":"0.시행문(업무연락)" if i==3 else "0.시행문(처리지침)" if i==4 else "0.시행문(센터)","prefix":"처리지침" if i==4 else "","spacing":"" if i==5 else " ","suffix":"","receiver":""} for i,label in enumerate(labels)]


def style(style_id):
    return next(x for x in catalog() if x["id"] == str(style_id))


@lru_cache(maxsize=6)
def template_body(style_id):
    from core import extract
    item=style(style_id)
    # User-supplied blank forms intentionally contain no example body to extract or generate.
    if item.get("blank_body"):return ""
    if not (template_root()/item["file"]).is_file():return ""
    text=extract(template_root()/item["file"])
    start=text.find(item["body_start"])
    end=text.find(item["signature"],start)
    if start<0 or end<=start:raise ValueError("기본 HWP의 본문 범위를 확인하지 못했습니다.")
    return text[start:end].strip()


def suggested_style(job,family="공문"):
    if family=="업무연락":return style("4")
    if family=="처리지침":return style("5")
    category=job.get("category","")
    if "중앙" in category:return style("2")
    if "시청" in category:
        return style("6" if any(k in job.get("title","") for k in ("협의","건축","주택","사업계획","사용승인","계획인가")) else "3")
    return style("1")


def destination(config, item):
    from core import ROOT
    return config.get("outgoing_folders", {}).get(item["id"]) or str(Path(config.get("root",ROOT)) / item["folder"])


def sequence(out):
    # Once registered, the ledger's own number is authoritative. Never reuse a sample's number.
    raw = str(out.get("number") or out.get("file_number", "")).strip()
    if not re.fullmatch(r"\d{1,6}", raw) or int(raw) < 1:
        raise ValueError("발신대장 등록 후 저장하거나, 사용할 발신번호 숫자를 입력해 주세요. 예시 공문의 번호는 재사용하지 않습니다.")
    if out.get("number") and out.get("file_number") and int(out["file_number"]) != int(raw):
        raise ValueError("입력한 번호가 이미 등록된 발신번호와 다릅니다.")
    return int(raw)


def filename(item, out, today=None):
    today = today or date.today()
    title = out["title"].strip()
    if not title or re.search(r'[<>:"/\\|?*\x00-\x1f]', title) or title.endswith((".", " ")):
        raise ValueError("HWP 제목과 파일명을 같게 저장하려면 제목에 파일명 금지 문자(\\ / : * ? 등)를 사용하지 마세요.")
    suffix = out.get("filename_suffix", item["suffix"]).strip()
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', suffix):
        raise ValueError("파일명 뒤 표시에 사용할 수 없는 문자가 있습니다.")
    return f"{item['prefix']}{sequence(out):03d}.{item['spacing']}{today:%y.%m.%d}. {title}{suffix}.hwp"


def template_preview(style_id):
    """Read the HWP's own thumbnail locally; never render a substitute form."""
    import olefile
    from PIL import Image
    source=template_root()/style(style_id)["file"]
    if not source.is_file():return None
    try:
        with olefile.OleFileIO(source) as ole:
            if not ole.exists("PrvImage"):return None
            raw=ole.openstream("PrvImage").read(8*1024*1024+1)
        if len(raw)>8*1024*1024:return None
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>16_000_000:return None
            return image.copy()
    except (OSError,ValueError,Image.DecompressionBombError):return None


def check_template(style_id):
    """Refuse a new ledger entry when its selected original form is unavailable."""
    item=style(style_id)
    source=template_root()/item["file"]
    if not source.is_file():raise ValueError("선택한 원본 HWP 양식이 없습니다. 양식을 먼저 설치해 주세요.")
    import olefile
    if not olefile.isOleFile(source):raise ValueError("선택한 HWP 양식 파일을 확인하지 못했습니다.")
    from core import extract
    text=_compact(extract(source))
    anchors=[*item.get("title_fragments",[]),item.get("old_number",""),item.get("old_date",""),item.get("receiver",""),item.get("old_sender","")]
    for anchor in anchors:
        if anchor and text.count(_compact(anchor))!=1:raise ValueError("원본 양식의 자동 입력 위치를 다시 확인하세요: "+anchor)
    return source


def native_values(item,out,today=None):
    """Use the ledger number in both the filename and the form's printed metadata."""
    today=today or date.today()
    seq=sequence(out)
    values={k:str(out.get(k,"")).strip() for k in ("title","receiver","sender","body")}
    fields={"year":today.year,"year2":today.year%100,"month":today.month,"day":today.day,"number":seq}
    pattern=item.get("date_pattern")
    if pattern:display=pattern.format(**fields)
    elif item.get("blank_body"):display=f"({today.year%100:02d}. {today.month}. {today.day})"
    else:display=f"({today:%Y. %m. %d.})"
    fields["date"]=display
    number_pattern=item.get("number_pattern")
    values["number_date"]=(number_pattern.format(**fields) if number_pattern else
        f"서울센터 {today.year}-{seq:03d}" if item["family"]=="공문" or item.get("blank_body") else f"{today.year}-{seq:03d} {display}")
    values["display_date"]=display
    return values


def _compact(text):
    return re.sub(r"\s+", "", text)


def _text(p):
    return "".join(n.text or "" for n in _paragraph_texts(p))


def _find(paragraphs, target):
    found = [p for p in paragraphs if _compact(target) in _compact(_text(p))]
    if len(found) != 1:
        raise ValueError("양식의 기입 위치를 하나로 확인하지 못했습니다: " + target[:45])
    return found[0]


def _replace(p, old, new):
    """Replace one literal span even when split across styled runs; retain surrounding run styles."""
    nodes = _paragraph_texts(p)
    original = "".join(n.text or "" for n in nodes)
    positions = [i for i, c in enumerate(original) if not c.isspace()]
    start = _compact(original).find(_compact(old))
    if start < 0:
        raise ValueError("양식 기입 위치가 바뀌었습니다.")
    left, right = positions[start], positions[start + len(_compact(old)) - 1] + 1
    cursor = 0
    inserted = False
    for n in nodes:
        text = n.text or ""
        end = cursor + len(text)
        if cursor < right and end > left:
            before = text[:max(0, left-cursor)]
            after = text[max(0, right-cursor):] if end > right else ""
            n.text = before + (new if not inserted else "") + after
            inserted = True
        cursor = end
    for cache in list(p):
        if cache.tag == f"{{{HP}}}linesegarray": p.remove(cache)


def _top(p, section):
    while p.getparent() is not section:
        p = p.getparent()
        if p is None: raise ValueError("양식 본문 영역을 찾을 수 없습니다.")
    return p


def prepare_hwpx(source, output, item, values):
    """Replace old letter content, including body tables, while preserving logo/header/footer styles."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with zipfile.ZipFile(source) as src:
        sections = [n for n in src.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)]
        if len(sections) != 1:
            raise ValueError("기본 공문의 구역 구조가 바뀌었습니다. 원본 양식을 다시 선택해 주세요.")
        section = etree.fromstring(src.read(sections[0]), parser)
        paragraphs = list(section.iter(f"{{{HP}}}p"))
        first = _find(paragraphs, item["body_start"])
        sign = _find(paragraphs, item["signature"])
        body_paragraphs=set(paragraphs[paragraphs.index(first):paragraphs.index(sign)])
        start, end = _top(first, section), _top(sign, section)
        a, b = section.index(start), section.index(end)
        if a >= b:
            raise ValueError("양식 본문과 서명 영역이 예상 구조와 다릅니다. 원본을 보존하고 저장을 중단했습니다.")
        if not values.get("keep_template_body"):
            container=section
            if start is not first:
                # 업무연락/처리지침 place header AND body in a text box: replace its body tail only.
                if item["family"] not in ("업무연락","처리지침"):
                    raise ValueError("기본 공문의 본문 영역 구조가 바뀌었습니다.")
                container=first.getparent()
                if container.tag!=f"{{{HP}}}subList":
                    raise ValueError("업무연락·처리지침의 본문 글상자 구조를 확인해 주세요.")
                for child in list(section)[a+1:b]:section.remove(child)
                a,b=container.index(first),len(container)
            seed = etree.Element(first.tag, dict(first.attrib))
            run = next((r for r in first if r.tag == f"{{{HP}}}run" and list(r.iter(f"{{{HP}}}t"))), None)
            if run is None: raise ValueError("본문 글자 서식을 찾을 수 없습니다.")
            newrun = etree.SubElement(seed, run.tag, dict(run.attrib))
            etree.SubElement(newrun, f"{{{HP}}}t")
            for child in list(container)[a:b]: container.remove(child)
            max_id = max((int(p.get("id", "0")) for p in paragraphs if p.get("id", "0").isdigit()), default=0)
            for offset, line in enumerate(values["body"].replace("\r\n", "\n").split("\n")):
                p = deepcopy(seed)
                p.set("id", str(max_id + offset + 1))
                _set_paragraph(p, line)
                container.insert(a + offset, p)
        # Resolve only retained original paragraphs; newly inserted body text may quote old metadata.
        paragraphs = [p for p in paragraphs if p not in body_paragraphs and section in p.iterancestors()]
        anchors=[*item["title_fragments"],item["receiver"],item["old_number"],item["old_sender"]]
        if item["old_date"]:anchors.append(item["old_date"])
        locations={anchor:_find(paragraphs,anchor) for anchor in anchors}
        for fragment in reversed(item["title_fragments"][1:]):
            _replace(locations[fragment], fragment, "")
        fragment=item["title_fragments"][0]
        _replace(locations[fragment], fragment, values["title"])
        _replace(locations[item["receiver"]], item["receiver"], values["receiver"])
        _replace(locations[item["old_number"]], item["old_number"], values["number_date"])
        if item["old_date"]:
            _replace(locations[item["old_date"]], item["old_date"], values["display_date"])
        _replace(locations[item["old_sender"]], item["old_sender"], values["sender"])
        # Invalidate all layout caches, including paragraphs holding resized title/table cells.
        for cache in list(section.iter(f"{{{HP}}}linesegarray")):
            cache.getparent().remove(cache)
        changed = etree.tostring(section, encoding="UTF-8", xml_declaration=True)
        with zipfile.ZipFile(output, "x") as dst:
            for info in src.infolist():
                if info.filename == sections[0]: data = changed
                elif info.filename == "Preview/PrvText.txt":
                    data = f"수신 {values['receiver']}\n제목 {values['title']}\n{values['body']}".encode("utf-8")
                elif info.filename=="Preview/PrvImage.png":
                    from PIL import Image
                    preview=io.BytesIO()
                    with Image.new("RGB",(32,32),"white") as image:image.save(preview,format="PNG")
                    data=preview.getvalue()
                else: data = src.read(info.filename)
                dst.writestr(info, data)


def replace_native_fields(hwp,item,values):
    """Edit text in the original HWP object. Never apply/recreate a style or reconstruct a paragraph."""
    from core import extract
    original=extract(template_root()/item["file"])
    title=values["title"]
    titles=[title]
    if len(item["title_fragments"])>1:
        match=re.fullmatch(r"(.+?)(\([^()]+\))",title.strip())
        titles=[match[1].rstrip(),match[2]] if match else [title,""]
    pairs=[*zip(item["title_fragments"],titles),
           (item["receiver"],values["receiver"]),(item["old_number"],values["number_date"]),
           (item["old_sender"],values["sender"])]
    if item.get("metadata_only"):pairs=[pair for pair in pairs if pair[0]]
    if item["old_date"]:pairs.append((item["old_date"],values["display_date"]))
    # Refuse ambiguous source anchors rather than accidentally editing a body reference.
    for old,new in pairs:
        if _compact(original).count(_compact(old))!=1:raise ValueError("원본 양식의 기입 위치가 중복되어 있습니다.")
        if any(c in new for c in "\r\n"):raise ValueError("제목·수신처·담당자에는 줄바꿈을 넣지 마세요.")
    # A new title/recipient may quote another old anchor. Replace that anchor before inserting the quote.
    pending=[pair for pair in pairs if pair[0]!=pair[1]];ordered=[]
    while pending:
        safe=next((pair for pair in pending if not any(other is not pair and _compact(other[0]) in _compact(pair[1]) for other in pending)),None)
        if safe is None:raise ValueError("새 제목·수신처의 문구가 기존 기입 위치와 겹칩니다. 문구를 확인해 주세요.")
        ordered.append(safe);pending.remove(safe)
    for old,new in ordered:
        hwp.HAction.Run("MoveDocBegin")
        params=hwp.HParameterSet.HFindReplace
        hwp.HAction.GetDefault("AllReplace",params.HSet)
        params.FindString=old;params.ReplaceString=new
        params.Direction=hwp.FindDir("AllDoc")
        params.ReplaceMode=1;params.IgnoreMessage=1;params.FindType=1
        params.UseWildCards=0;params.FindRegExp=0;params.WholeWordOnly=0
        params.IgnoreFindString=0;params.IgnoreReplaceString=0
        params.FindStyle="";params.ReplaceStyle=""
        # Some Hancom versions return False after a successful wrap-around replacement.
        # The saved result is verified below instead of trusting that return value.
        hwp.HAction.Execute("AllReplace",params.HSet)


def export_hwp(item, out, output, factory=None):
    """A private Hancom automation instance; original template and existing files are never overwritten."""
    today = date.today()
    seq = sequence(out)
    for key in ("title", "receiver", "sender"):
        if not out.get(key, "").strip(): raise ValueError("공문 저장에 필요한 항목을 입력해 주세요: " + {"title":"제목","receiver":"수신 기관","sender":"담당자","body":"본문"}[key])
    if out.get("ledger_type") != item["family"]:
        raise ValueError("선택한 HWP 양식과 발신대장 유형이 다릅니다.")
    output = Path(output)
    if output.name != filename(item, out, today):
        raise ValueError("파일명은 발신번호·현재 날짜·HWP 제목에 따라 자동 생성됩니다. 제목 또는 번호를 수정해 주세요.")
    if output.exists(): raise FileExistsError("같은 이름의 공문이 이미 있습니다. 덮어쓰지 않았습니다.")
    values = native_values(item,out,today)
    # Native HWP always keeps the source body, including its paragraph-level formatting.
    # An edited plain-text draft remains available to paste into the existing HWP paragraphs.
    expected_body=template_body(item["id"])
    out["hwp_body_paste_needed"]=values["body"]!=expected_body
    coinit = False
    hwp = None
    try:
        if factory is None:
            import pythoncom
            from win32com.client import DispatchEx
            pythoncom.CoInitialize(); coinit = True
            try: hwp = DispatchEx("HWPFrame.HwpObject")
            except Exception as error:
                raise RuntimeError("HWP 저장에는 이 PC에 설치된 한컴 한글(편집 가능한 Windows 버전)이 필요합니다. 한글 설치 상태를 확인해 주세요.") from error
        else: hwp = factory()
        # Hancom may request its normal file-access permission on first use; do not suppress it.
        with tempfile.TemporaryDirectory(prefix="seoul-hwp-") as temporary:
            work = Path(temporary)
            native = work/"result.hwp"
            source = template_root()/item["file"]
            if not hwp.Open(str(source.resolve()), "HWP", ""):
                raise RuntimeError("기본 HWP 양식을 열지 못했습니다.")
            replace_native_fields(hwp,item,values)
            if not hwp.SaveAs(str(native), "HWP", "") or not native.is_file():
                raise RuntimeError("한글에서 HWP 저장을 완료하지 못했습니다.")
            from core import extract
            text = extract(native)
            check_title=not item.get("metadata_only") or bool(item.get("title_fragments"))
            if (check_title and _compact(values["title"]) not in _compact(text)) or _compact(expected_body) not in _compact(text):
                raise RuntimeError("저장된 HWP의 제목·본문 검증에 실패했습니다. 공유폴더에는 저장하지 않았습니다.")
            for field in ("receiver","sender","number_date"):
                anchor={"receiver":"receiver","sender":"old_sender","number_date":"old_number"}[field]
                if item.get("metadata_only") and not item.get(anchor):continue
                if _compact(values[field]) not in _compact(text):raise RuntimeError("저장된 HWP의 수신처·담당자·시행번호 확인에 실패했습니다.")
            if item["old_date"] and _compact(values["display_date"]) not in _compact(text):raise RuntimeError("저장된 HWP 시행일 확인에 실패했습니다.")
            from hwp_fidelity import verify
            verify(source,native)
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                with output.open("xb") as dst, native.open("rb") as src: shutil.copyfileobj(src, dst)
            except Exception:
                # Only remove a file we created, never an existing destination.
                if 'dst' in locals(): output.unlink(missing_ok=True)
                raise
        return str(output)
    finally:
        if hwp is not None:
            try: hwp.Clear(1); hwp.Quit()
            except Exception: pass
        if coinit:
            import pythoncom
            pythoncom.CoUninitialize()
