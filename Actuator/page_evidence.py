"""Bounded, read-only page evidence for generation and failure diagnosis."""
import asyncio
import json

DOM_EVIDENCE = r'''(options = {}) => {
  const known = new Set(options.known_visible || []);
  const attrs = ['id','class','name','type','role','aria-label','aria-selected','aria-checked',
    'aria-expanded','aria-controls','aria-invalid','aria-haspopup','placeholder','title','data-testid','data-test','data-date'];
  const selector = el => {
    const tag=el.tagName.toLowerCase();
    if(el.id && document.querySelectorAll('#'+CSS.escape(el.id)).length===1) return '#'+CSS.escape(el.id);
    for(const a of ['data-testid','data-test','name','aria-label','placeholder']){
      const v=el.getAttribute(a);
      if(v){const s=tag+'['+a+'='+JSON.stringify(v)+']';
        if(document.querySelectorAll(s).length===1)return s;}
    }
    if(el.name){
      for(const a of ['aria-label','placeholder','type']){
        const v=el.getAttribute(a);
        if(v){
          const s=tag+'[name='+JSON.stringify(el.name)+']['+a+'='+JSON.stringify(v)+']';
          if(document.querySelectorAll(s).length===1)return s;
        }
      }
    }
    let n=el, parts=[];
    for(;n && n.nodeType===1;n=n.parentElement){
      if(n.id && document.querySelectorAll('#'+CSS.escape(n.id)).length===1){
        parts.unshift('#'+CSS.escape(n.id));return parts.join(' > ');
      }
      if(n.classList.length){
        const scoped=n.tagName.toLowerCase()+Array.from(n.classList).slice(0,3).map(c=>'.'+CSS.escape(c)).join('');
        const candidate=[scoped,...parts].join(' > ');
        if(document.querySelectorAll(candidate).length===1)return candidate;
      }
      const siblings=n.parentElement?Array.from(n.parentElement.children).filter(s=>s.tagName===n.tagName):[];
      parts.unshift(n.tagName.toLowerCase()+(siblings.length>1?':nth-of-type('+(siblings.indexOf(n)+1)+')':''));
      if(document.querySelectorAll(parts.join(' > ')).length===1)return parts.join(' > ');
    }
    return parts.join(' > ');
  };
  const describe = el => {
    const r=el.getBoundingClientRect();
    let visible=r.width>0&&r.height>0, opacity=1;
    for(let n=el;n;n=n.parentElement){
      const st=getComputedStyle(n);opacity*=Number(st.opacity);
      if(st.display==='none'||st.visibility==='hidden'||n.hidden||n.getAttribute('aria-hidden')==='true')visible=false;
    }
    const sensitive=el.matches('input[type=password]')||/password|secret|token|cookie/i.test((el.name||'')+' '+(el.id||''));
    const a=Object.fromEntries(attrs.filter(k=>el.hasAttribute(k)).map(k=>[k,el.getAttribute(k).slice(0,180)]));
    const result = {tag:el.tagName.toLowerCase(),attrs:a,css:selector(el),text:(el.innerText||el.textContent||'').trim().slice(0,100),
      value:sensitive?'[REDACTED]':('value' in el?String(el.value).slice(0,120):undefined),
      visible:visible&&opacity>.01,disabled:el.disabled||false,readOnly:el.readOnly||false,
      box:[r.x,r.y,r.width,r.height].map(Math.round),
      ancestors:Array.from((function*(n){for(let i=0;n&&i<3;i++,n=n.parentElement)yield n;})(el.parentElement))
        .map(n=>({tag:n.tagName.toLowerCase(),id:n.id,class:typeof n.className==='string'?n.className.slice(0,100):''}))};
    if(options.compact)delete result.ancestors;
    if(result.visible && (el.matches('input,select,textarea,[role=alert],[aria-invalid],[aria-selected=true],[aria-checked=true]') || /error|invalid|selected|checked|current/i.test(el.className))){
      const st=getComputedStyle(el);
      result.styles={color:st.color,borderColor:st.borderColor,backgroundColor:st.backgroundColor,outlineColor:st.outlineColor};
    }
    return result;
  };
  // Include hidden input siblings (strict-locator ambiguity) and custom controls.
  const elements=[...document.querySelectorAll('input,button,select,textarea,[role],label,output,th,td,li,[data-testid],[data-test],[onclick],[class*="calendar"],[class*="calendar"] *,[class*="date-picker"] *,[class*="datepicker"] *,[role=listbox] *,[role=dialog] *,[class*="date-"],[class*="switch"],[class*="exchange"],[class*="swap"],[class*="error"],[class*="select"],[class*="checkbox"]')];
  // Large navigation/results pages must not crowd the active popup out of a
  // bounded snapshot. Keep selected state and newly revealed controls before
  // repeated background navigation; this ordering never changes the page.
  const overlay='[role=dialog],[role=listbox],[role=menu],[aria-modal=true],[class*="calendar"],[class*="picker"],[class*="popover"],[class*="dropdown"]';
  const priority=(n,el)=>{
    if(!n.visible)return 6;
    if(el.matches('[aria-selected=true],[aria-checked=true]') || /(^|[\s_-])(selected|checked|current|active)([\s_-]|$)/i.test(n.attrs.class||''))return 0;
    if(el.closest(overlay))return 1;
    if(['input','select','textarea'].includes(n.tag))return 2;
    if(options.known_visible && !known.has(n.css))return 3;
    if(n.tag==='button')return 4;
    return 5;
  };
  const nodes=elements.map(el=>{const node=describe(el);return {node,rank:priority(node,el)};})
    .sort((a,b)=>a.rank-b.rank).map(item=>item.node);
  return {total:nodes.length,truncated:nodes.length>220,nodes:nodes.slice(0,220)};
}'''


async def capture_page_evidence(page, known_visible=None):
    evidence = {'url': page.url}
    secrets = []
    try:
        # Playwright's ARIA snapshot can include password values, even for masked inputs.
        # Read only for local redaction; never send these values to the server/model.
        secrets = await page.locator('input,textarea').evaluate_all(
            """els => els.filter(e => e.type==='password'||/password|secret|token|cookie/i.test((e.name||'')+' '+e.id))
                .map(e=>e.value).filter(Boolean)""")
        evidence['title'] = await page.title()
        evidence['aria_snapshot'] = (await page.locator('body').aria_snapshot(timeout=3000))[:20000]
        options = {'compact': True}
        if known_visible is not None:
            options['known_visible'] = known_visible
        dom = await asyncio.wait_for(page.evaluate(DOM_EVIDENCE, options), timeout=4)
        # Keep valid JSON even if a page has unusually verbose attributes.
        while len(json.dumps(dom, ensure_ascii=False)) > 39000 and dom['nodes']:
            dom['nodes'].pop()
            dom['truncated'] = True
        evidence['dom'] = json.dumps(dom, ensure_ascii=False)
    except Exception as exc:
        evidence['note'] = '页面证据读取不完整：' + type(exc).__name__
    for key, value in evidence.items():
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, '[REDACTED]')
            evidence[key] = value
    return evidence
