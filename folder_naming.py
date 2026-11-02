"""Preview existing naming styles and create per-document folders without overwriting."""
from datetime import date
from pathlib import Path, PureWindowsPath
import json
import re
import string

AGENCIES = {
    "기초센터 공문": ["00.기초센터 공문"],
    "중앙센터 공문": ["0.중앙센터 공문", "5.보건복지부 및 중앙처리지침", "8.실태조사"],
    "서울시청 공문": ["0.시청공문", "1.건축허가&사업계획승인", "2.사용승인&사용검사", "8.실태조사"],
}
TEMPLATES = ("{date}_{title}", "{ymd}_{title}", "{dotdate} {title}", "{year}-{number:03d} {title}", "{number:03d}. {title}", "{title}")
CHILD_KINDS=("incoming","outgoing","reply")
AGENCY_SUFFIX=re.compile(r"(\s*\((?:[^()\r\n]*(?:센터|시청|중앙|기초|서울시)[^()\r\n]*(?:발송|제출|회신|협의|접수)|시청|중앙|센터|기초센터|25개구)\))$")


def child_folders(agency,selected):
    if any(k not in CHILD_KINDS for k in selected):raise ValueError("하위 폴더 선택이 올바르지 않습니다.")
    prefix={"기초센터 공문":"기초","중앙센터 공문":"중앙","서울시청 공문":"시청"}[agency]
    labels={"incoming":prefix+"공문","outgoing":"센터발송","reply":prefix+"회신"}
    return {key:f"{i}.{labels[key]}" for i,key in enumerate((k for k in CHILD_KINDS if k in selected),1)}


def agency_for(category):
    return "중앙센터 공문" if "중앙" in category else "기초센터 공문" if "기초" in category else "서울시청 공문" if "시청" in category else ""


def safe_component(text):
    mapping = str.maketrans({'<':'＜','>':'＞',':':'：','"':'＂','/':'／','\\':'＼','|':'｜','?':'？','*':'＊'})
    text = re.sub(r"[\x00-\x1f]", " ", text).translate(mapping).strip().rstrip(". ")
    if not text:
        raise ValueError("폴더 이름이 비어 있습니다.")
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", text, re.I): text = "_" + text
    if len(text.encode("utf-16-le")) > 400:
        raise ValueError("폴더 이름이 너무 깁니다. 제목 또는 앞 형식을 줄여 주세요.")
    return text


def folder_name(template, title, day, number=None):
    day = date.fromisoformat(day)
    suffix=AGENCY_SUFFIX.search(template)
    if suffix:
        tag=suffix[0].strip()
        if title.rstrip().endswith(tag):title=title.rstrip()[:-len(tag)].rstrip()
    values = {"title": title, "date": day.isoformat(), "ymd": day.strftime("%Y%m%d"), "dotdate": day.strftime("%Y.%m.%d"),
              "year": day.year, "yy":day.year%100, "month": day.month, "day": day.day, "number": int(number) if number is not None else None}
    result, titles = [], 0
    for literal, field, spec, conversion in string.Formatter().parse(template):
        result.append(literal)
        if field is None: continue
        if field not in values or conversion or (spec and not re.fullmatch(r"0?[1-6]d", spec)):
            raise ValueError("폴더 형식에는 제목·날짜·접수번호 항목만 사용할 수 있습니다.")
        if field == "title": titles += 1
        value = values[field]
        result.append("폴더순번" if field == "number" and number is None else format(value, spec))
    if titles != 1:
        raise ValueError("폴더 형식에 {title}(선택한 제목)이 한 번 들어가야 합니다.")
    return safe_component("".join(result))


def infer_template(name):
    # Preserve separators and number padding. Unsupported prefixes require explicit choice.
    suffix=AGENCY_SUFFIX.search(name)
    if suffix:
        body=name[:suffix.start()]
        if body:
            literal=suffix[0].replace("{","{{").replace("}","}}")
            return (infer_template(body) or "{title}")+literal
    enclosed=re.match(r"^(\[|\()(\d{1,5})(\]|\))([ ._-]*)(.+)$",name)
    if enclosed and (enclosed[1],enclosed[3]) in (("[","]"),("(",")")):
        rest=infer_template(enclosed[5]) or "{title}"
        if "{number" not in rest:return enclosed[1]+"{number:0"+str(len(enclosed[2]))+"d}"+enclosed[3]+enclosed[4]+rest
    named=re.match(r"^((?:시행문|공문|업무연락|처리지침|협의|회신|접수)\s*(?:제\s*)?)(\d{1,5})([ ._-]+)(.+)$",name)
    if named:
        rest=infer_template(named[4]) or "{title}"
        if "{number" not in rest:return named[1]+"{number:0"+str(len(named[2]))+"d}"+named[3]+rest
    spaced=re.match(r"^((?:20)?\d{2})([.-]\s*)(\d{1,2})([.-]\s*)(\d{1,2})([ ._-]+)(.+)$",name)
    if spaced and spaced[2].strip()==spaced[4].strip() and (" " in spaced[2] or " " in spaced[4]):
        try:date(int(spaced[1]) if len(spaced[1])==4 else 2000+int(spaced[1]),int(spaced[3]),int(spaced[5]))
        except ValueError:return None
        return ("{year}" if len(spaced[1])==4 else "{yy:02d}")+spaced[2]+("{month:02d}" if len(spaced[3])==2 else "{month}")+spaced[4]+("{day:02d}" if len(spaced[5])==2 else "{day}")+spaced[6]+(infer_template(spaced[7]) or "{title}")
    patterns = [
        (r"^((?:20)?\d{2})([-.])(\d{1,2})\2(\d{1,2})([ ._\-)]+)(.+)$", "date"),
        (r"^(20\d{6})([ ._\-)]+)(.+)$", "ymd"),
        (r"^(20\d{2})([-_])(\d{3,5})([ ._\-)]+)(.+)$", "year_number"),
        (r"^(\d{1,5})([ ._\-)]+)(.+)$", "number"),
    ]
    for pattern, kind in patterns:
        match = re.match(pattern, name)
        if not match: continue
        g = match.groups()
        if kind == "date":
            try: date(int(g[0]) if len(g[0])==4 else 2000+int(g[0]), int(g[2]), int(g[3]))
            except ValueError: continue
            prefix = ("{year}" if len(g[0])==4 else "{yy:02d}") + g[1] + ("{month:02d}" if len(g[2])==2 else "{month}") + g[1] + ("{day:02d}" if len(g[3])==2 else "{day}") + g[4]
            nested = infer_template(g[5])
            return prefix + (nested or "{title}")
        if kind == "ymd":
            try: date(int(g[0][:4]), int(g[0][4:6]), int(g[0][6:]))
            except ValueError: continue
            return "{ymd}" + g[1] + (infer_template(g[2]) or "{title}")
        if kind == "year_number":
            nested = infer_template(g[4])
            return "{year}" + g[1] + "{number:0" + str(len(g[2])) + "d}" + g[3] + (nested if nested and "{number" not in nested else "{title}")
        if kind == "number":
            if 1900 <= int(g[0]) <= 2100: return None
            nested = infer_template(g[2])
            if g[2][0].isdigit() and (not nested or "{number" in nested): return None
            return "{number:0" + str(len(g[0])) + "d}" + g[1] + (nested if nested and "{number" not in nested else "{title}")
    return None


def destination(root, relative):
    windows = PureWindowsPath(relative)
    if windows.is_absolute() or windows.drive or ".." in windows.parts or not windows.parts:
        raise ValueError("공유 실적 폴더 안의 저장 위치를 선택해 주세요.")
    root = Path(root).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise ValueError(f"저장 폴더에 접근할 수 없습니다: {relative}")
    return target


def describe_folder(root, relative):
    directory = destination(root, relative)
    return {**describe_directory(directory),"relative":relative}


def describe_directory(directory):
    directory=Path(directory).resolve()
    if not directory.is_absolute() or not directory.is_dir():raise ValueError("기존 폴더를 읽을 저장 위치를 선택하세요.")
    names = sorted((p.name for p in directory.iterdir() if p.is_dir() and not p.name.startswith(".")), reverse=True)
    examples = names[:100]
    styles = {}
    for example in examples:
        template = infer_template(example)
        if template: styles.setdefault(template, []).append(example)
    ordered = sorted(styles, key=lambda t: len(styles[t]), reverse=True)
    return {"path":str(directory), "examples": examples[:8], "names":names,"next_number":sequence_from_names(names), "styles": [{"template": t, "samples": styles[t][:3]} for t in ordered]}


def preferred_template(record,config,slot):
    saved=config.get("folder_profiles",{}).get(slot,{})
    if record["names"]:
        if saved.get("path")==record["path"] and saved.get("template"):return saved["template"]
        if record["styles"]:return record["styles"][0]["template"]
    return saved.get("template") or config.get("route_templates",{}).get(slot.removeprefix("incoming:")) or config.get("folder_defaults",{}).get("template") or "{title}"


def remember_profile(config,slot,path,template,agency,children,save_in="outgoing"):
    profile={"path":str(Path(path).resolve()),"template":template,"agency":agency,"children":list(children),"save_in":save_in}
    config.setdefault("folder_profiles",{})[slot]=profile
    config["folder_defaults"]=profile.copy()
    config.setdefault("folder_children",{})[agency]=list(children)
    return {key:config[key] for key in ("folder_profiles","folder_defaults","folder_children")}


def read_sequence(name, template):
    pattern, found = [], False
    for literal, field, spec, conversion in string.Formatter().parse(template):
        pattern.append(re.escape(literal))
        if field is None: continue
        if field == "number":
            if found: return None
            pattern.append(r"(?P<number>\d+)")
            found = True
        elif field == "title": pattern.append(r".+")
        elif field in ("year","yy","month","day","ymd"): pattern.append(r"\d+")
        elif field in ("date","dotdate"): pattern.append(r"[\d.-]+")
        else: return None
    match = re.fullmatch("".join(pattern),name)
    return int(match.group("number")) if match and found else None


def sequence_from_names(names, selected_template=None):
    numbers = []
    for name in names:
        for template in (selected_template,infer_template(name)):
            if template:
                number = read_sequence(name,template)
                if number is not None: numbers.append(number)
    return max(numbers,default=0)+1


def next_sequence(directory,selected_template=None):
    return sequence_from_names([p.name for p in Path(directory).iterdir() if p.is_dir() and not p.name.startswith(".")],selected_template)


def plan_folders(job, root):
    preflight(job,root)
    for target in job["routing"]["targets"]:
        if target.get("folder_number") is None and any(f=="number" for _,f,_,_ in string.Formatter().parse(target["template"])):
            target["folder_number"] = next_sequence(destination(root,target["relative"]),target["template"])
        target["folder_name"] = folder_name(target["template"],target.get("folder_title") or job["routing"].get("folder_title") or job["title"],job["date"],target.get("folder_number"))


def route_tree(root, agency):
    records = []
    for relative in AGENCIES[agency]:
        record = describe_folder(root, relative)
        records.append(record)
        if relative.startswith("5."):
            for child in sorted(destination(root, relative).iterdir()):
                if child.is_dir() and re.match(r"^[01][. ]", child.name) and ("지침" in child.name or "업무연락" in child.name):
                    record = describe_folder(root, str(Path(relative) / child.name))
                    record["parent"] = relative
                    records.append(record)
    return records


def preflight(job, root):
    routing = job.get("routing", {})
    agency = routing.get("agency")
    targets = routing.get("targets", [])
    if agency not in AGENCIES or not targets:
        raise ValueError("공문 종류와 저장 위치를 먼저 선택해 주세요.")
    child_folders(agency,routing.get("children",[]))
    seen = set()
    for target in targets:
        relative = target["relative"]
        if relative in seen: raise ValueError("같은 저장 위치를 두 번 선택했습니다.")
        seen.add(relative)
        if not any(relative == p or PureWindowsPath(relative).is_relative_to(PureWindowsPath(p)) for p in AGENCIES[agency]):
            raise ValueError("선택한 공문 종류에 포함된 저장 폴더를 선택해 주세요.")
        destination(root, relative)
        folder_name(target["template"], target.get("folder_title") or routing.get("folder_title") or job["title"], job["date"], target.get("folder_number"))
    from core import digest
    for file in job["files"]:
        if not Path(file["path"]).is_file() or digest(file["path"]) != file["hash"]:
            raise ValueError("원본 첨부파일이 없거나 바뀌었습니다. 다운로드 파일을 확인해 주세요.")


def copy_to_subfolders(job, root, on_progress=None):
    from core import copy_files
    preflight(job, root)
    plan_folders(job,root)
    results = []
    for target in job["routing"]["targets"]:
        parent = destination(root, target["relative"])
        name = target["folder_name"]
        folder = parent / name
        marker = folder / ".수신등록.json"
        if folder.exists():
            if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("id") != job["id"]:
                raise ValueError(f"같은 이름의 기존 폴더가 있습니다. 기존 폴더는 변경하지 않았습니다: {folder}")
        else:
            folder.mkdir()
            with marker.open("x", encoding="utf-8") as stream:
                json.dump({"id": job["id"], "folder_number":target.get("folder_number"), "receipt_number":job.get("number",""), "title": job["title"]}, stream, ensure_ascii=False)
            import os
            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(marker),2)
        job["files_started"] = True
        if on_progress: on_progress()
        children=child_folders(job["routing"]["agency"],job["routing"].get("children",[]))
        for child in children.values():(folder/child).mkdir(exist_ok=True)
        # Incoming attachments belong to the source-letter folder when it is selected.
        incoming=children.get("incoming")
        relative=str(Path(name)/incoming) if incoming else name
        results.extend(copy_files(job, parent, subfolders=[relative]))
    return results
