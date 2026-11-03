"""Align copied TSV to physical sheet rows, including a hidden leading title row."""
import csv
import io
import re


def bounds(value):
    match=re.fullmatch(r"\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?",str(value).strip().upper())
    if not match:raise ValueError("시트에서 선택된 셀 주소를 확인하지 못했습니다.")
    def col(text):
        result=0
        for char in text:result=result*26+ord(char)-64
        return result
    c1,r1=col(match[1]),int(match[2]);c2,r2=col(match[3] or match[1]),int(match[4] or match[2])
    if r1<1 or c1>c2 or r1>r2:raise ValueError("시트 선택 범위가 올바르지 않습니다.")
    return r1,c1,r2,c2


def align(text,requested,selected):
    r1,c1,r2,c2=bounds(requested);s1,d1,s2,d2=bounds(selected)
    if d1!=c1 or d2!=c2 or s2!=r2 or s1<r1 or (s1!=r1 and r1!=1):
        raise ValueError("요청한 셀 범위와 실제 선택 범위가 다릅니다. 숨김·필터 또는 병합 셀을 확인해 주세요.")
    width=c2-c1+1
    rows=list(csv.reader(io.StringIO(text),delimiter="\t")) if text else [[""]]
    if len(rows)!=s2-s1+1 or any(len(row)>width for row in rows) or not any(len(row)==width for row in rows):
        raise ValueError("복사된 행·열 수가 선택 범위와 다릅니다. 필터로 제외된 행 또는 복사 상태를 확인해 주세요.")
    return [[""]*width for _ in range(s1-r1)]+[row+[""]*(width-len(row)) for row in rows]
