"""Read-only HWP 5 format checks: original style slots and paragraph formatting must survive."""
import struct
import zlib


def records(data):
    pos=0
    while pos<len(data):
        if pos+4>len(data):raise ValueError("손상된 HWP 서식 레코드입니다.")
        header=struct.unpack_from("<I",data,pos)[0];pos+=4
        tag,size=header&1023,header>>20
        if size==4095:
            if pos+4>len(data):raise ValueError("손상된 HWP 서식 길이입니다.")
            size=struct.unpack_from("<I",data,pos)[0];pos+=4
        if pos+size>len(data):raise ValueError("HWP 서식 범위가 올바르지 않습니다.")
        yield tag,data[pos:pos+size]
        pos+=size


def snapshot(path):
    import olefile
    with olefile.OleFileIO(path) as ole:
        header=ole.openstream("FileHeader").read()
        if not header.startswith(b"HWP Document File") or len(header)<40:raise ValueError("HWP 5 원본 양식을 확인하지 못했습니다.")
        flags=struct.unpack_from("<I",header,36)[0]
        if flags&6:raise ValueError("암호·배포용 HWP의 스타일은 확인할 수 없습니다.")
        def stream(name):
            data=ole.openstream(name).read()
            if flags&1:
                decoder=zlib.decompressobj(-15)
                data=decoder.decompress(data,100*1024*1024+1)
                if decoder.unconsumed_tail or len(data)>100*1024*1024:raise ValueError("HWP 서식 데이터가 너무 큽니다.")
            return data
        # Font / border / character / tab / numbering / bullet / paragraph / named style tables.
        tables={tag:[] for tag in range(19,27)}
        for tag,payload in records(stream("DocInfo")):
            if tag in tables:tables[tag].append(payload)
        names=[]
        for payload in tables[26]:
            if len(payload)<2:raise ValueError("손상된 HWP 스타일입니다.")
            length=struct.unpack_from("<H",payload)[0]
            names.append(payload[2:2+length*2].decode("utf-16-le"))
        paragraphs=[];paper=[];current=None
        sections=sorted(n for n in ole.listdir() if len(n)==2 and n[0]=="BodyText" and n[1].startswith("Section"))
        for section in sections:
            for tag,payload in records(stream(section)):
                if tag==66:
                    if len(payload)<12:raise ValueError("손상된 HWP 문단 모양입니다.")
                    current={"paragraph":struct.unpack_from("<H",payload,8)[0],"style":payload[10],"characters":[]}
                    paragraphs.append(current)
                elif tag==68 and current is not None:
                    for pos in range(0,len(payload)-7,8):
                        charshape=struct.unpack_from("<I",payload,pos+4)[0]
                        if not current["characters"] or current["characters"][-1]!=charshape:current["characters"].append(charshape)
                elif tag in (73,74,75):paper.append((tag,payload))
        if len(names)<10 or not paragraphs:raise ValueError("원본 HWP의 스타일과 본문을 확인하지 못했습니다.")
        return {"tables":tables,"names":names,"paragraphs":paragraphs,"paper":paper}


def verify(original,result):
    before,after=snapshot(original),snapshot(result)
    # Extra unused definitions may be appended by Hancom, but existing slots must be unchanged.
    for tag,entries in before["tables"].items():
        if after["tables"][tag][:len(entries)]!=entries:
            raise ValueError("원본 HWP의 스타일·글자·문단 모양이 달라 저장을 중단했습니다. 공유폴더에는 저장하지 않았습니다.")
    if before["names"]!=after["names"][:len(before["names"])]:
        raise ValueError("원본 HWP 스타일 순서가 달라 저장을 중단했습니다.")
    if before["paragraphs"]!=after["paragraphs"] or before["paper"]!=after["paper"]:
        raise ValueError("원본 HWP의 문단 스타일·내어쓰기 또는 용지 서식 유지 확인에 실패했습니다. 공유폴더에는 저장하지 않았습니다.")
    return before["names"]
