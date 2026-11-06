"""Defaults for relief applications; folder numbering stays separate from ledger numbers."""
import re
import string
from pathlib import Path

LOCATION='3.완화신청'
TITLE='완화된 세부기준의 승인신청에 따른 의견 요청'
FOLDER_TITLE='완화된 세부기준의 승인신청에 따른 의견요청'
ADDRESS=re.compile(r'([가-힣0-9·]+(?:동|읍|면|리|가|로|길))\s*(산\s*)?(\d+(?:\s*-\s*\d+)?)(?:번지)?')


def address(text):
    match=ADDRESS.search(text)
    if not match:return ''
    return match[1]+' '+('산 ' if match[2] else '')+re.sub(r'\s+','',match[3])


def source_fields(job):
    title=job.get('title','').strip()
    match=re.search(r'\(([^()]*)\)([^()]*)$',title)
    subject=match[1].strip() if match else ''
    suffix=match[2].strip() if match else ''
    filenames=[Path(f.get('path','')).stem for f in job.get('files',[])]
    file_address=next((a for name in filenames if (a:=address(name))), '')
    if file_address:subject=file_address
    elif not subject or any(w in subject for w in ('신청서','붙임','공문')):
        subject=address(title) or address(job.get('text',''))
    # Only copy an explicitly written district; never infer a district from a street address.
    text=' '.join(str(job.get(k,'')) for k in ('issuer','title','text'))
    match=re.search(r'(종로|중|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동)구(?:청)?',text)
    return {'district':match[1]+'구청' if match else '', 'subject':subject,'suffix':suffix}


def receipt_title(subject,suffix=''):
    return TITLE+('('+subject.strip()+')' if subject.strip() else '')+suffix.strip()


def folder_title(district,subject,suffix=''):
    return '['+(district.strip() or '구청명 입력')+'] '+FOLDER_TITLE+('('+subject.strip()+')' if subject.strip() else '')+suffix.strip()


def default_template(previous=''):
    number='03d'
    for _,field,spec,_ in string.Formatter().parse(previous):
        if field=='number':number=spec;break
    return '{number'+(':'+number if number else '')+'}.{yy:02d}.{month:02d}.{day:02d}._{title}'


def validate(values):
    if not re.fullmatch(r'[^\[\]{}\\/\r\n\t]+구청',values.get('district','').strip()):raise ValueError('구청명을 강남구청과 같은 형식으로 입력하세요.')
    if not values.get('subject','').strip():raise ValueError('완화신청의 주소·사업명을 입력하세요.')
    if not values.get('receipt_title','').strip():raise ValueError('수신접수 제목을 입력하세요.')
