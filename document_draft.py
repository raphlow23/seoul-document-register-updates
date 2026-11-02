"""Editable document drafting and non-destructive HWPX template filling."""
from copy import deepcopy
from pathlib import Path
import re
import shutil
import zipfile

PURPOSES=("전달·안내","회신","협조요청","직접 작성")
ALIASES={"제목":"title","수신처":"receiver","수신":"receiver","발신자":"sender","발송자":"sender","담당자":"sender",
         "본문":"body","내용":"body","붙임":"attachments","발신일":"date","날짜":"date","문서번호":"document_number",
         "발신번호":"document_number","수신번호":"receipt_number","관련문서":"reference","신청부서":"department","날인자":"stamp_person",
         **{k:k for k in ("title","receiver","sender","body","attachments","date","document_number","receipt_number","reference","department","stamp_person")}}
TOKEN=re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
HP="http://www.hancom.co.kr/hwpml/2011/paragraph"


def reflow(text):
    paragraphs=[]
    current=[]
    for raw in text.replace("\r\n","\n").split("\n"):
        line=raw.strip()
        boundary=bool(re.match(r"^(?:\d+[.)]|[가-하][.)]|[○□ㅇ•-]|수신|제목|붙임|관련문서|끝\.)",line))
        if not line or boundary:
            if current: paragraphs.append(" ".join(current)); current=[]
            if not line: paragraphs.append(""); continue
        current.append(line)
    if current: paragraphs.append(" ".join(current))
    return "\n".join(paragraphs).strip()


def build_body(out):
    purpose=out.get("purpose","전달·안내")
    if purpose=="직접 작성": return out.get("body","")
    summary=out.get("summary","").strip()
    if not summary: raise ValueError("핵심 전달·회신 내용을 입력해 주세요. 검토 결과나 사실을 임의로 만들지 않습니다.")
    pieces=["1. 귀 기관의 무궁한 발전을 기원합니다."]
    reference=out.get("reference","").strip()
    number=2
    if reference:
        pieces.append(f"{number}. {reference}와 관련입니다.")
        number+=1
    lead={"전달·안내":"다음과 같이 안내하오니 업무에 참고하여 주시기 바랍니다.",
          "회신":"요청하신 사항에 대하여 다음과 같이 회신합니다.",
          "협조요청":"다음 사항에 대하여 협조하여 주시기 바랍니다."}[purpose]
    pieces.append(f"{number}. {lead}")
    pieces.append(summary)
    attachments=out.get("attachments","").strip()
    if attachments: pieces.append("붙임  "+attachments+"  끝.")
    else: pieces.append("끝.")
    return "\n\n".join(pieces)


def field_values(job,out):
    stamp=out.get("stamp",{})
    return {"title":out.get("title") or job["title"],"receiver":out.get("receiver",""),"sender":out.get("sender",""),
            "body":out.get("body",""),"attachments":out.get("attachments",""),"date":out.get("date",""),
            "document_number":out.get("document_number",""),"receipt_number":job.get("number",""),
            "reference":out.get("reference",""),"department":stamp.get("department","편의증진부"),"stamp_person":stamp.get("person","")}


def full_text(job,out):
    values=field_values(job,out)
    return (f"수신  {values['receiver']}\n제목  {values['title']}\n문서번호  {values['document_number']}\n발신일  {values['date']}\n\n"
            +values["body"]+f"\n\n발송자  {values['sender']}")


def _paragraph_texts(paragraph):
    return [t for t in paragraph.iter(f"{{{HP}}}t") if next(t.iterancestors(f"{{{HP}}}p"),None) is paragraph]


def _set_paragraph(paragraph,text):
    nodes=_paragraph_texts(paragraph)
    if not nodes: raise ValueError("양식 표식의 문단 구조를 확인해 주세요.")
    # This path is only for plain text paragraphs; keep their existing paragraph/run styles.
    for index,node in enumerate(nodes):
        for child in list(node): node.remove(child)
        node.text=text if index==0 else ""
    for node in list(paragraph):
        if node.tag==f"{{{HP}}}linesegarray": paragraph.remove(node)


def _replace_inline(paragraph,nodes,values):
    pieces=[node.text or "" for node in nodes]
    starts=[]
    offset=0
    for piece in pieces: starts.append(offset); offset+=len(piece)
    text="".join(pieces)
    for match in reversed(list(TOKEN.finditer(text))):
        first=next(i for i,piece in enumerate(pieces) if starts[i]<=match.start()<starts[i]+len(piece))
        last=next(i for i,piece in enumerate(pieces) if starts[i]<=match.end()-1<starts[i]+len(piece))
        replacement=str(values.get(ALIASES[match.group(1).strip()],""))
        left=pieces[first][:match.start()-starts[first]]
        right=pieces[last][match.end()-starts[last]:]
        if first==last: pieces[first]=left+replacement+right
        else:
            pieces[first]=left+replacement
            for i in range(first+1,last): pieces[i]=""
            pieces[last]=right
    for node,piece in zip(nodes,pieces): node.text=piece
    for node in list(paragraph):
        if node.tag==f"{{{HP}}}linesegarray": paragraph.remove(node)


def fill_hwpx(template,output,values):
    from lxml import etree
    template,output=Path(template),Path(output)
    if template.resolve()==output.resolve(): raise ValueError("기본 양식은 덮어쓸 수 없습니다. 새 파일 이름을 선택해 주세요.")
    if template.suffix.lower()!=".hwpx":
        raise ValueError("자동 기입은 HWPX 양식에서 지원합니다. HWP 양식은 한글에서 HWPX로 한 번 저장하거나 ‘양식 사본 열기’와 본문 복사를 사용하세요.")
    if output.suffix.lower()!=".hwpx":raise ValueError("자동 기입한 새 문서는 .hwpx 확장자로 저장해 주세요.")
    parser=etree.XMLParser(resolve_entities=False,no_network=True,remove_blank_text=False)
    contents={}
    changes=0
    with zipfile.ZipFile(template) as source:
        if len(source.infolist())>5000 or sum(x.file_size for x in source.infolist())>200*1024*1024:
            raise ValueError("양식 파일이 너무 큽니다.")
        sections=[n for n in source.namelist() if re.fullmatch(r"Contents/section\d+\.xml",n)]
        if not sections: raise ValueError("HWPX 본문을 찾지 못했습니다.")
        max_id=0
        roots={}
        for name in sections:
            root=etree.fromstring(source.read(name),parser)
            roots[name]=root
            max_id=max(max_id,max((int(p.get("id","0")) for p in root.iter(f"{{{HP}}}p") if p.get("id","0").isdigit()),default=0))
        for name,root in roots.items():
            for paragraph in list(root.iter(f"{{{HP}}}p")):
                nodes=_paragraph_texts(paragraph)
                text="".join(t.text or "" for t in nodes)
                matches=list(TOKEN.finditer(text))
                if not matches: continue
                unknown=[m.group(1).strip() for m in matches if m.group(1).strip() not in ALIASES]
                if unknown: raise ValueError("지원하지 않는 양식 표식: "+", ".join(unknown))
                replaced=TOKEN.sub(lambda m:str(values.get(ALIASES[m.group(1).strip()],"")),text)
                if any(n.tag in (f"{{{HP}}}tbl",f"{{{HP}}}pic",f"{{{HP}}}ole") for n in paragraph.iter()):
                    raise ValueError("양식의 표식은 표 셀 안의 일반 문단 또는 본문 문단에 넣어 주세요.")
                lines=replaced.replace("\r\n","\n").split("\n")
                if len(lines)==1:
                    _replace_inline(paragraph,nodes,values)
                    changes+=len(matches)
                    continue
                if len(matches)!=1 or text.strip()!=matches[0].group(0):
                    raise ValueError("여러 문단으로 들어갈 본문 표식은 별도 문단에 {{본문}}만 넣어 주세요.")
                parent=paragraph.getparent()
                index=parent.index(paragraph)
                # Clone the paragraph style for new body paragraphs; remove obsolete layout caches.
                for offset,line in enumerate(lines):
                    target=paragraph if offset==0 else deepcopy(paragraph)
                    if offset:
                        max_id+=1
                        target.set("id",str(max_id))
                        parent.insert(index+offset,target)
                    _set_paragraph(target,line)
                changes+=len(matches)
            contents[name]=etree.tostring(root,encoding="UTF-8",xml_declaration=True)
        if not changes:
            raise ValueError("양식에 {{제목}}, {{수신처}}, {{본문}} 등의 기입 표식이 없습니다. 기본 양식 파일을 보내주시면 기입 위치를 맞출 수 있습니다.")
        output.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive creation preserves any existing document; the original template is unchanged.
        with output.open("xb") as file:
            with zipfile.ZipFile(file,"w") as result:
                for info in source.infolist():
                    data=contents.get(info.filename)
                    if data is None:
                        data=(full_preview(values).encode("utf-8") if info.filename=="Preview/PrvText.txt" else source.read(info.filename))
                    result.writestr(info,data)
    return {"path":str(output),"fields_filled":changes}


def full_preview(values):
    return f"수신 {values.get('receiver','')}\n제목 {values.get('title','')}\n\n{values.get('body','')}"


def template_copy(template,output):
    template,output=Path(template),Path(output)
    if template.resolve()==output.resolve(): raise ValueError("원본 양식은 덮어쓸 수 없습니다.")
    if template.suffix.lower() not in (".hwp",".hwpx") or output.suffix.lower()!=template.suffix.lower():
        raise ValueError("HWP/HWPX 양식을 원래 확장자의 새 한글 파일로 저장해 주세요.")
    with template.open("rb") as src,output.open("xb") as dst: shutil.copyfileobj(src,dst)
    return str(output)
