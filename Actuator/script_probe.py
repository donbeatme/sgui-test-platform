"""Read the configured entry in an isolated context, without form submissions."""
import asyncio
import json
import sys
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from page_evidence import capture_page_evidence


async def observe(job):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=job.get('headless', False))
        try:
            context = await browser.new_context(viewport={'width':1440,'height':900})
            page = await context.new_page()
            response = await page.goto(job['base_url'], wait_until='domcontentloaded', timeout=45000)
            await page.locator('body').wait_for(state='visible', timeout=10000)
            # DOMContentLoaded can contain only a SPA header. Give the initial load
            # a bounded readiness window; never wait for networkidle (ads/polling).
            try:
                await page.wait_for_load_state('load', timeout=10000)
            except PlaywrightTimeout:
                pass
            # Read-only readiness check; do not click arbitrary menus or dismiss dialogs.
            await page.wait_for_function(
                "() => document.readyState !== 'loading' && (document.body.innerText.trim().length > 0 || document.querySelector('input,button,select,[role]'))",
                timeout=10000)
            # Hydration may run after load. Wait for relevant DOM mutations to
            # settle, bounded so clocks, ads and long polling cannot hold the job.
            await page.evaluate("""() => new Promise(resolve => {
                let quiet, deadline;
                const finish=()=>{observer.disconnect();clearTimeout(quiet);clearTimeout(deadline);resolve();};
                const changed=()=>{clearTimeout(quiet);quiet=setTimeout(finish,600);};
                const observer=new MutationObserver(changed);
                observer.observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true});
                deadline=setTimeout(finish,3500);changed();
            })""")
            evidence = await capture_page_evidence(page)
            evidence['probe_version'] = 3
            evidence['http_status'] = response.status if response else None
            if response and response.status >= 400:
                return {'status':'blocked','observation':evidence,
                        'summary':f'入口返回 HTTP {response.status}，已保存实际页面；请确认访问权限或网站状态后重试。'}
            # Explore only non-submitting form-field entrances in this isolated
            # browser: no fill, key press, checkbox, link or action button.
            # This captures real dynamic dropdown/calendar markup instead of
            # asking the model to invent it from an unopened entry page.
            base_dom=json.loads(evidence.get('dom','{}'))
            previous={n['css']:n for n in base_dom.get('nodes',[])}
            fields=[n for n in base_dom.get('nodes',[]) if n.get('tag')=='input' and n.get('visible')
                and n.get('attrs',{}).get('type','text') in ('text','search','date')
                and not any(word in str(n.get('attrs',{})).lower() for word in ('password','secret','token'))][:5]
            explorations=[]
            for field in fields:
                try:
                    target=page.locator(field['css'])
                    if await target.count()!=1 or not await target.is_visible():
                        continue
                    await target.click(timeout=2500)
                    await page.evaluate("""() => new Promise(resolve => {
                        let quiet, deadline;
                        const done=()=>{observer.disconnect();clearTimeout(quiet);clearTimeout(deadline);resolve();};
                        const changed=()=>{clearTimeout(quiet);quiet=setTimeout(done,300);};
                        const observer=new MutationObserver(changed);
                        observer.observe(document.body,{subtree:true,childList:true,attributes:true});
                        deadline=setTimeout(done,1500);changed();
                    })""")
                    opened=await capture_page_evidence(page,known_visible=[
                        css for css,n in previous.items() if n.get('visible')])
                    nodes=json.loads(opened.get('dom','{}')).get('nodes',[])
                    changed=[{k:n[k] for k in ('css','attrs','text','box','value') if k in n}
                        for n in nodes if n.get('visible') and
                        (n['css'] not in previous or not previous[n['css']].get('visible') or
                         n.get('attrs')!=previous[n['css']].get('attrs') or n.get('text')!=previous[n['css']].get('text'))]
                    observation={'action':'click field entrance','target':field['css'],
                        'target_attributes':field.get('attrs',{}),'url':page.url,'visible_changes':changed,'truncated':False}
                    while len(json.dumps(observation,ensure_ascii=False))>7000 and observation['visible_changes']:
                        observation['visible_changes'].pop();observation['truncated']=True
                    explorations.append(observation)
                    previous={n['css']:n for n in nodes}
                except Exception as exc:
                    explorations.append({'target':field['css'],'note':type(exc).__name__})
            evidence['field_observations']=explorations
            return {'status':'pass','observation':evidence,'summary':'已读取入口页面。'}
        except Exception as exc:
            return {'status':'blocked','summary':'入口页面观察未完成：'+str(exc)[:3000]}
        finally:
            await browser.close()


if __name__ == '__main__':
    job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    result = asyncio.run(observe(job))
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
