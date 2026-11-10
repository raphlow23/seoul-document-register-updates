"""Read the visible Sheets tab bar; never infer a ledger from a partial name."""
import json


def probe(name):
    return """(()=>{
const wanted="""+json.dumps(name)+""";
const norm=s=>String(s||'').normalize('NFKC').replace(/[\\u200b-\\u200d\\ufeff]/g,'').replace(/\\s+/g,' ').trim();
const nodes=[...document.querySelectorAll('.docs-sheet-tab-name')];
const matches=nodes.filter(n=>norm(n.textContent)===norm(wanted));
const active=document.querySelector('.docs-sheet-active-tab .docs-sheet-tab-name');
const n=matches.length===1?matches[0]:null;
const rect=n?.getBoundingClientRect();
const x=rect?(Math.max(0,rect.left)+Math.min(innerWidth,rect.right))/2:0;
const y=rect?(Math.max(0,rect.top)+Math.min(innerHeight,rect.bottom))/2:0;
return {names:nodes.map(n=>n.textContent.trim()),matches:matches.length,currentIndex:nodes.indexOf(active),targetIndex:nodes.indexOf(n),
active:matches.length===1&&!!active&&norm(active.textContent)===norm(wanted),x,y,
visible:!!rect&&rect.width>0&&rect.height>0&&rect.right>0&&rect.left<innerWidth&&rect.bottom>0&&rect.top<innerHeight};
})()"""
