"""Per-template draft titles; existing registered titles remain authoritative."""
import re


def template_text(item,field):
    if field=='title':
        text=item.get('source_title') or ' '.join(item.get('title_fragments',[]))
    else:
        text=item.get('source_receiver') or item.get('receiver','')
    return '' if '{{' in text or '}}' in text else text.strip()


def default_title(config,item):
    titles=config.get('outgoing_default_titles',{})
    return titles.get(str(item['id']),template_text(item,'title')).strip()


def save_default_title(config,item,title):
    title=re.sub(r'\s+',' ',str(title)).strip()
    if '\0' in title:raise ValueError('기본 제목에 사용할 수 없는 문자가 있습니다.')
    values=dict(config.get('outgoing_default_titles',{}))
    key=str(item['id'])
    if title==template_text(item,'title'):values.pop(key,None)
    else:values[key]=title
    config['outgoing_default_titles']=values
    return title
