"""Pick one repeated metadata span while preserving all native HWP formatting."""
from pathlib import Path
import re
import shutil
import struct
import zlib


def spans(text,anchor):
    letters=''.join(anchor.split())
    if not letters:return []
    return list(re.finditer('[ \t]*'.join(re.escape(c) for c in letters),text))


def occurrences(text,anchor):
    return [(m.start(),re.sub(r'\s+',' ',text[max(0,m.start()-35):m.end()+35]).strip()) for m in spans(text,anchor)]


def prepare(source,target,anchors,choices,text):
    import olefile
    import pythoncom
    from win32com import storagecon
    from core import clean_hwp_text,extract
    from hwp_fidelity import verify
    from hwp_documents import _compact
    source,target=Path(source),Path(target)
    with olefile.OleFileIO(source) as ole:
        original={tuple(p):ole.openstream(p).read() for p in ole.listdir() if ole.get_type(p)==2}
    header=original.get(('FileHeader',),b'')
    if len(header)<40 or not header.startswith(b'HWP Document File'):raise ValueError('지원되는 HWP 5 양식이 아닙니다.')
    flags=struct.unpack_from('<I',header,36)[0]
    if flags&6:raise ValueError('암호·배포용 문서는 양식으로 등록할 수 없습니다.')
    sections={};paragraphs=[]
    for name in sorted(n for n in original if len(n)==2 and n[0]=='BodyText' and n[1].startswith('Section')):
        packed=original[name]
        if flags&1:
            dec=zlib.decompressobj(-15);raw=dec.decompress(packed,100*1024*1024+1)
            if dec.unconsumed_tail or len(raw)>100*1024*1024:raise ValueError('HWP 본문이 너무 큽니다.')
        else:raw=packed
        sections[name]=bytearray(raw);pos=0
        while pos<len(raw):
            if pos+4>len(raw):raise ValueError('손상된 HWP 레코드입니다.')
            word=struct.unpack_from('<I',raw,pos)[0];pos+=4;tag=word&1023;size=word>>20
            if size==4095:
                if pos+4>len(raw):raise ValueError('손상된 HWP 레코드입니다.')
                size=struct.unpack_from('<I',raw,pos)[0];pos+=4
            if pos+size>len(raw):raise ValueError('손상된 HWP 본문입니다.')
            if tag==67:paragraphs.append((name,pos,size,clean_hwp_text(raw[pos:pos+size])))
            pos+=size
    result=dict(anchors);edits=[];expected=text;all_text=_compact(text)
    for key,anchor in anchors.items():
        if not anchor or all_text.count(_compact(anchor))<=1:continue
        matches=spans(text,anchor)
        chosen=choices.get(key)
        if len(matches)!=all_text.count(_compact(anchor)) or not isinstance(chosen,int) or isinstance(chosen,bool) or not 0<=chosen<len(matches):
            raise ValueError('같은 문구가 여러 곳 있습니다. 자동 입력할 위치를 선택하세요: '+anchor)
        found=[]
        for section,offset,size,plain in paragraphs:
            payload=bytes(sections[section][offset:offset+size]);at=0
            for match in spans(plain,anchor):
                needle=match.group().encode('utf-16-le');at=payload.find(needle,at)
                if at<0 or at%2:raise ValueError('지정 문구가 제어문자에 걸쳐 있습니다. 연속된 문구를 선택하세요.')
                found.append((section,offset+at,needle))
                at+=len(needle)
        if len(found)!=len(matches):raise ValueError('입력 위치가 표·제어문자에 걸쳐 있습니다. 한 문단 안의 연속된 문구를 선택하세요.')
        section,at,needle=found[chosen]
        token=next(('{{S'+str(i)+'}}' for i in range(10000) if '{{S'+str(i)+'}}' not in expected),None)
        if token is None or len(token.encode('utf-16-le'))>len(needle):raise ValueError('위치 지정 문구를 번호·날짜 전체로 길게 선택하세요.')
        token=token+' '*((len(needle)-len(token.encode('utf-16-le')))//2)
        if any(s==section and max(a,at)<min(a+n,at+len(needle)) for s,a,n in edits):raise ValueError('서로 겹치는 위치를 지정할 수 없습니다.')
        sections[section][at:at+len(needle)]=token.encode('utf-16-le');edits.append((section,at,len(needle)))
        start,end=matches[chosen].span();expected=expected[:start]+token+expected[end:]
        result[key]=token
    shutil.copy2(source,target)
    if not edits:return result
    storage=body=stream=None
    pythoncom.CoInitialize()
    try:
        mode=storagecon.STGM_READWRITE|storagecon.STGM_SHARE_EXCLUSIVE
        storage=pythoncom.StgOpenStorage(str(target),None,mode);body=storage.OpenStorage('BodyText',None,mode,None,0)
        for section in set(e[0] for e in edits):
            raw=bytes(sections[section])
            if flags&1:
                comp=zlib.compressobj(level=9,wbits=-15);raw=comp.compress(raw)+comp.flush()
            stream=body.OpenStream(section[1],None,mode,0);stream.Seek(0,0);stream.Write(raw);stream.SetSize(len(raw));stream.Commit(0);stream=None
        body.Commit(0);body=None;storage.Commit(0);storage=None
    finally:
        stream=body=storage=None
        pythoncom.CoUninitialize()
    verify(source,target)
    with olefile.OleFileIO(target) as ole:
        if sorted(original)!=sorted(tuple(p) for p in ole.listdir() if ole.get_type(p)==2):raise ValueError('양식 구성 검증에 실패했습니다.')
        changed={e[0] for e in edits}
        if any(ole.openstream(list(p)).read()!=data for p,data in original.items() if p not in changed):raise ValueError('양식 서식·그림이 변경되어 등록을 중단합니다.')
    if extract(target)!=expected.strip():raise ValueError('지정한 위치 외의 본문이 달라져 등록을 중단합니다.')
    return result
