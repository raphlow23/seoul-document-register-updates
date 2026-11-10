"""Per-template draft titles; existing registered titles remain authoritative."""
import re


def default_title(config,style_id):
    return str(config.get('outgoing_default_titles',{}).get(str(style_id),'')).strip()


def save_default_title(config,style_id,title):
    title=re.sub(r'\s+',' ',str(title)).strip()
    if '\0' in title:raise ValueError('기본 제목에 사용할 수 없는 문자가 있습니다.')
    values=dict(config.get('outgoing_default_titles',{}))
    if title:values[str(style_id)]=title
    else:values.pop(str(style_id),None)
    config['outgoing_default_titles']=values
    return title


def title_for_choice(config,style_id,previous_id,out):
    if out.get('sheet_attempted') or str(previous_id or '')==str(style_id):return None
    return default_title(config,style_id) or None
