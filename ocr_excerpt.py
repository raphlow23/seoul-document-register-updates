"""Extract useful official-letter paragraphs without rewriting their OCR content."""
import re


def excerpt(text,start=2,attachments=True):
    lines=text.replace("\r\n","\n").split("\n")
    compact=lambda s:re.sub(r"\s","",s)
    attachment=next((i for i,l in enumerate(lines) if re.match(r"^붙임\s*\d*",compact(l))),len(lines))
    if start is None:
        result="\n".join(lines if attachments else lines[:attachment]).strip()
        if not result:raise ValueError("가져올 본문이 없습니다.")
        return result
    else:
        begin=next((i for i,l in enumerate(lines[:attachment]) if re.match(r"^\s*"+str(int(start))+r"\s*[.)]\s*[^\d\s]",l)),None)
        if begin is None:raise ValueError(f"본문 {start}항을 찾지 못했습니다. OCR 원문에서 필요한 구간을 선택하고 ‘선택한 글 정리’를 사용하세요.")
    end=len(lines)
    for i in range(begin+1,len(lines)):
        c=compact(lines[i]).strip("_ ")
        if re.match(r"^(?:시행|협조자|전화|전하|전송|팩스|www\.)",c) or re.fullmatch(r"(?:사단법인)?[가-힣()]{2,40}(?:협회장|기술지원센터장)",c):
            end=i;break
    if not attachments:end=min(end,attachment)
    result="\n".join(lines[begin:end]).strip()
    if attachments:
        endings=list(re.finditer(r"끝\s*[.。]",result))
        if endings:result=result[:endings[-1].end()]
    if not result:raise ValueError("가져올 본문이 없습니다. 선택 범위를 확인해 주세요.")
    return result


def paragraphs(text):
    result=[];current=[]
    def flush():
        if current:result.append(" ".join(current));current.clear()
    for raw in text.splitlines():
        line=raw.strip()
        boundary=re.match(r"^(?:\d+\s*[.)]|[가-하]\s*[.)]|[A-Za-z]{1,3}[,.]|붙\s*임|[*※○□•=])",line)
        if boundary or not line:flush()
        if not line:
            if result and result[-1]!="":result.append("")
        else:current.append(line)
    flush()
    return result


def clean(text,config,start=2,attachments=True,spacing=True):
    chosen=excerpt(text,start,attachments)
    if len(chosen)>20000:raise ValueError("필요한 본문 구간을 20,000자 이내로 선택해 주세요.")
    from korean_spacing import correct
    fixed=[]
    for p in paragraphs(chosen):
        if len(p)>4000:raise ValueError("한 문단이 너무 깁니다. 문단을 나누거나 필요한 구간을 선택해 주세요.")
        fixed.append(correct(p,config) if spacing and p else p)
    result="\n".join(fixed).strip()
    if re.sub(r"\s","",chosen)!=re.sub(r"\s","",result):raise ValueError("원문 글자 보존 확인에 실패했습니다.")
    suspicious=sorted(set(re.findall(r"\b(?:HE|Eo|of|ZE|Zt)\b|[\^_]",result)))
    return {"text":result,"warnings":suspicious,"source":chosen}
