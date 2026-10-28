import asyncio
import socket
import threading
import unittest
from pathlib import Path
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from script_runner import execute
from script_probe import observe
from page_evidence import capture_page_evidence
from playwright.async_api import async_playwright


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/unavailable':
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if self.path == '/denied':
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'<h1>Access denied</h1>')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        if self.path == '/dismiss':
            self.wfile.write(b'''<!doctype html><title>Dismiss fixture</title>
<button id="close" onclick="panel.hidden=true;counter.textContent=String(Number(counter.textContent)+1)">Close choices</button>
<output id="counter">0</output><section id="panel">Choices</section><button id="outside">Neutral</button>''')
            return
        if self.path.startswith('/hydrating'):
            self.wfile.write(b'''<!doctype html><title>Hydration fixture</title><header>Loading interface</header>
<script>setTimeout(()=>{const f=document.createElement('form');f.innerHTML='<input name="business-field" aria-label="Business field">';document.body.appendChild(f);},250);</script>''')
            return
        if self.path.startswith('/unlabelled-form'):
            self.wfile.write(b'''<!doctype html><title>Custom form</title>
<main><div><section><div><input placeholder="Choose a place" onclick="document.querySelector('[role=listbox]').hidden=false"></div></section></div></main>
<div role="listbox" hidden><div role="option">First city</div></div>
<button onclick="document.body.textContent='SUBMITTED'">Submit</button>''')
            return
        if self.path.startswith('/custom-fields'):
            self.wfile.write(b'''<!doctype html><title>Custom fields</title>
<button id="toggle" onclick="this.dataset.state=this.dataset.state==='on'?'off':'on';counter.textContent=String(Number(counter.textContent)+1)" data-state="on">Include extras</button>
<output id="counter">0</output>
<button id="open" onclick="panel.style.display='block'">Choose tier</button>
<span id="chosen">Standard</span><div id="panel" style="display:none"><button onclick="chosen.textContent='Premium';panel.style.display='none'">Premium</button></div>
<div style="display:grid;grid-template-columns:80px 80px;width:160px"><span id="h1">Column A</span><span id="h2">Column B</span><span id="c1">Value A</span><span id="c2">Value B</span></div>''')
            return
        if self.path.startswith('/states'):
            ready = 'none' if 'unknown' in self.path else 'block'
            dialog = 'block' if 'blocked' in self.path else 'none'
            self.wfile.write(f'''<!doctype html><meta charset="utf-8"><title>Workflow fixture</title>
<header><button>登录</button></header>
<form aria-label="工作区" style="display:{ready}">
<label>名称<input aria-label="名称"></label>
<button type="button" onclick="document.querySelector('output').textContent=document.querySelector('input').value">应用</button><output></output>
</form>
<section data-testid="cart-ready" style="display:{ready}">购物车已加载</section>
<div role="dialog" aria-label="访问受限" style="display:{dialog}">需要完成验证</div>
'''.encode('utf-8'))
            return
        if self.path.startswith('/popup'):
            self.wfile.write('''<!doctype html><meta charset="utf-8"><title>Popup fixture</title>
<input id="value" readonly><button id="trigger" onclick="opens++;panel.style.opacity=(panel.style.display!=='none'&&panel.style.opacity==='1')?'0':'1';panel.style.display='block'">打开</button>
<output id="opens"></output>
<ul id="other"><li role="listitem" title="北京">北京</li></ul>
<ul id="panel" style="opacity:0;position:absolute;top:100px;background:white;width:200px">
<li role="listitem" title="北京" id="option" onmouseenter="if((location.search==='?recover'&&!recovered)||location.search==='?always-recover'){recovered=true;panel.style.display='none'}"
onclick="document.querySelector('#value').value='北京';panel.style.opacity='0'">北京</li>
<li role="option" data-date="2026-10-22" onclick="document.querySelector('#value').value='2026-10-22';panel.style.opacity='0'">22</li></ul>
<script>let opens=0,recovered=false;const panel=document.querySelector('#panel');
if(location.search==='?open')panel.style.opacity='1';
if(location.search==='?closing'){
  panel.style.opacity='1';
  panel.animate([{transform:'translateX(0px)'},{transform:'translateX(30px)'}],{duration:400});
  setTimeout(()=>panel.style.display='none',200);
}
setInterval(()=>document.querySelector('#opens').textContent=opens,20);</script>'''.encode('utf-8'))
            return
        self.wfile.write(b'''<!doctype html><title>Replay fixture</title><h1>Replay fixture</h1>
<label>Name<input aria-label="Name"></label><button onclick="document.querySelector('output').textContent=document.querySelector('input').value">Apply</button><output></output>
<div class="city-panel" style="display:none">Departure</div><div class="city-panel" style="opacity:0">Arrival</div>''')
    def log_message(self, *_):
        pass


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def job(self, code):
        return {'code':code,'parameters':{'base_url':f'http://127.0.0.1:{self.server.server_port}'},'headless':True,
            'timeout_seconds':20,'snapshot':{'steps':[{'step_number':1,'expected_result':'Fixture heading'}]}}

    def test_dismiss_panel_is_idempotent_and_does_not_accept_failed_closure(self):
        async def scenario():
            from replay_actions import PopupActions
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(f'http://127.0.0.1:{self.server.server_port}/dismiss')
                    helper = PopupActions()
                    with self.assertRaises(AssertionError):
                        await helper.dismiss_panel(panel=page.locator('#panel'), dismiss=page.locator('#outside'), timeout_ms=500)
                    await helper.dismiss_panel(panel=page.locator('#panel'), dismiss=page.locator('#close'))
                    await helper.dismiss_panel(panel=page.locator('#panel'), dismiss=page.locator('#close'))
                    self.assertEqual(await page.locator('#counter').inner_text(), '1')
                finally:
                    await browser.close()
        asyncio.run(scenario())
    def test_probe_reads_real_dom_without_actions(self):
        result = asyncio.run(observe({'base_url':f'http://127.0.0.1:{self.server.server_port}/states',
                                      'headless':True}))
        self.assertEqual(result['status'],'pass')
        self.assertIn('名称',result['observation']['dom'])
        self.assertIn('工作区',result['observation']['aria_snapshot'])
    def test_probe_waits_for_hydration_instead_of_returning_header(self):
        result=asyncio.run(observe({'base_url':f'http://127.0.0.1:{self.server.server_port}/hydrating','headless':True}))
        self.assertEqual(result['status'],'pass')
        self.assertIn('business-field',result['observation']['dom'])
    def test_probe_explores_field_without_form_or_id_ancestor(self):
        result=asyncio.run(observe({'base_url':f'http://127.0.0.1:{self.server.server_port}/unlabelled-form','headless':True}))
        self.assertEqual(result['status'],'pass')
        fields=result['observation']['field_observations']
        self.assertEqual(len(fields),1)
        self.assertTrue(any(n.get('text')=='First city' for n in fields[0]['visible_changes']))
        self.assertNotIn('SUBMITTED',str(fields))
    def test_probe_http_denial_is_blocked_with_actual_evidence(self):
        result=asyncio.run(observe({'base_url':f'http://127.0.0.1:{self.server.server_port}/denied','headless':True}))
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['observation']['http_status'],403)
        self.assertIn('Access denied',result['observation']['aria_snapshot'])
    def test_dom_candidates_are_unique_and_new_popup_has_priority(self):
        async def inspect():
            async with async_playwright() as p:
                browser=await p.chromium.launch(headless=True)
                try:
                    page=await browser.new_page()
                    nested='<div>'*9+'<input name="repeated">'+'</div>'*9
                    await page.set_content('<main>'+nested+nested+'</main>'+
                        '<button>Unrelated</button>'*230+
                        '<div class="calendar" hidden><span class="weekday">Monday</span><span class="day">21</span></div>')
                    baseline=await capture_page_evidence(page)
                    nodes=json.loads(baseline['dom'])['nodes']
                    fields=[n for n in nodes if n['tag']=='input']
                    self.assertEqual(len(fields),2)
                    self.assertNotEqual(fields[0]['css'],fields[1]['css'])
                    for field in fields:
                        self.assertEqual(await page.locator(field['css']).count(),1)
                    await page.locator('.calendar').evaluate('(el)=>el.hidden=false')
                    opened=await capture_page_evidence(page,known_visible=[n['css'] for n in nodes if n['visible']])
                    new_nodes=json.loads(opened['dom'])['nodes']
                    self.assertTrue(any(n['attrs'].get('class')=='weekday' for n in new_nodes))
                finally:
                    await browser.close()
        asyncio.run(inspect())
    def test_custom_toggle_is_idempotent_and_text_choice_checks_real_value(self):
        job=self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.set_toggle(control=page.locator("#toggle"),selected=page.locator("#toggle[data-state=on]"),unselected=page.locator("#toggle[data-state=off]"),checked=False)
        await record.set_toggle(control=page.locator("#toggle"),selected=page.locator("#toggle[data-state=on]"),unselected=page.locator("#toggle[data-state=off]"),checked=False)
        await expect(page.locator("#counter")).to_have_text("1")
        await record.choose_option(trigger=page.locator("#open"),panel=page.locator("#panel"),value=page.locator("#chosen"),option_role="button",option_name="Premium",expected_value="Premium",value_mode="text")
        await expect(page.locator("#chosen")).to_have_text("Premium")
''')
        job['parameters']['base_url']+='/custom-fields'
        outcome=asyncio.run(execute(job))
        self.assertEqual(outcome['status'],'pass',outcome)

    def test_custom_toggle_unknown_state_never_clicks_or_passes(self):
        job=self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.set_toggle(control=page.locator("#toggle"),selected=page.locator("#toggle[data-state=unknown1]"),unselected=page.locator("#toggle[data-state=unknown2]"),checked=False,timeout_ms=500)
        await expect(page.locator("#counter")).to_have_text("0")
''')
        job['parameters']['base_url']+='/custom-fields'
        self.assertEqual(asyncio.run(execute(job))['status'],'blocked')
    def test_alignment_checks_real_columns_and_rejects_wrong_header(self):
        code='''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.expect_aligned(target=page.locator("#c1"),reference=page.locator("#h1"),axis="column",timeout_ms=500)
        await expect(page.locator("#c1")).to_have_text("Value A")
'''
        job=self.job(code);job['parameters']['base_url']+='/custom-fields'
        self.assertEqual(asyncio.run(execute(job))['status'],'pass')
        job['code']=code.replace('reference=page.locator("#h1")','reference=page.locator("#h2")')
        self.assertEqual(asyncio.run(execute(job))['status'],'fail')

    def test_page_evidence_redacts_password_and_preserves_duplicate_context(self):
        async def check():
            async with async_playwright() as p:
                browser=await p.chromium.launch()
                try:
                    page=await browser.new_page()
                    await page.set_content('<section><input name="password" type="password" value="dont-store-this"><button>Apply</button></section><aside><button>Apply</button></aside>')
                    return await capture_page_evidence(page)
                finally:
                    await browser.close()
        result=asyncio.run(check())
        self.assertNotIn('dont-store-this',str(result))
        self.assertIn('[REDACTED]',result['dom'])
        self.assertIn('section',result['dom'])
        self.assertIn('aside',result['dom'])
    def test_real_browser_pass_and_screenshot(self):
        outcome=asyncio.run(execute(self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await page.get_by_label("Name").fill("SGUI")
        await page.get_by_role("button", name="Apply").click()
        await expect(page.locator("output")).to_have_text("SGUI")
''')))
        self.assertEqual(outcome['status'],'pass',outcome['summary'])
        self.assertTrue(outcome['steps'][0]['screenshot'].startswith('iVBOR'))
    def test_real_assertion_failure_is_fail(self):
        outcome=asyncio.run(execute(self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await expect(page.locator("h1")).to_have_text("Wrong title", timeout=300)
''')))
        self.assertEqual(outcome['status'],'fail',outcome['summary'])
        self.assertEqual(outcome['steps'][0]['status'],'fail')
        self.assertIn('Replay fixture', outcome['diagnostics']['aria_snapshot'])
        self.assertEqual(outcome['diagnostics']['step_number'], 1)

    def test_observation_records_real_state_and_stops_without_false_pass(self):
        outcome = asyncio.run(execute(self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.observe_state(states={"正常表单":page.get_by_label("Name"), "登录阻断":page.get_by_role("dialog", name="必须登录")})
        await record.stop(reason="按原预期记录状态后停止，不判定为业务通过")
''')))
        self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
        self.assertIn('最近观察到：正常表单', outcome['summary'])
        self.assertIn('实际页面状态：正常表单', outcome['steps'][0]['actual'])
        self.assertTrue(outcome['steps'][0]['screenshot'])
        self.assertIn('aria_snapshot', outcome['diagnostics'])

    def test_unknown_or_ambiguous_state_never_passes(self):
        for locator in ['page.get_by_role("dialog", name="必须登录")', 'page.locator(".city-panel")']:
            with self.subTest(locator=locator):
                outcome = asyncio.run(execute(self.job(f'''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.observe_state(states={{"观察状态":{locator}}}, timeout_ms=500)
''')))
                self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
                self.assertTrue(outcome['steps'][0]['screenshot'])

    def guarded(self, path, normal='continue', extra='', locator='page.get_by_role("form", name="工作区")'):
        code = f'''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.guard_state(states={{"场景适用":{locator},"阻断界面":page.get_by_role("dialog",name="访问受限")}},
            outcomes={{"场景适用":"{normal}","阻断界面":"blocked"}}, timeout_ms=800)
{extra}
'''
        job = self.job(code)
        job['parameters']['base_url'] += '/states/' + path
        return asyncio.run(execute(job))

    def test_normal_page_continues_business_actions_despite_header_login_button(self):
        outcome = self.guarded('ready', extra='''        await page.get_by_label("名称").fill("SGUI")
        await page.get_by_role("button",name="应用").click()
        await expect(page.locator("output")).to_have_text("SGUI")''')
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])
        self.assertEqual(outcome['steps'][0]['condition']['outcome'], 'continue')

    def test_condition_not_triggered_is_skip_for_different_page_structures(self):
        for locator in ['page.get_by_role("form", name="工作区")', 'page.get_by_test_id("cart-ready")']:
            outcome = self.guarded('ready', normal='skip', locator=locator)
            self.assertEqual(outcome['status'], 'skip', outcome['summary'])
            self.assertEqual(outcome['steps'][0]['condition']['matched'], ['场景适用'])
            self.assertTrue(outcome['steps'][0]['screenshot'])
            self.assertNotIn('ReplayNotApplicable', outcome['steps'][0]['actual'])

    def test_visible_blocking_dialog_wins_over_background_normal_page(self):
        for normal in ['continue', 'skip']:
            outcome = self.guarded('blocked', normal=normal)
            self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
            self.assertIn('阻断界面', outcome['steps'][0]['condition']['matched'])

    def test_unknown_state_is_blocked_not_skipped(self):
        outcome = self.guarded('unknown', normal='skip')
        self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
        self.assertNotIn('condition', outcome['steps'][0])

    def test_continuation_does_not_hide_business_assertion_failure(self):
        outcome = self.guarded('ready', extra='        await expect(page.locator("output")).to_have_text("不存在的结果", timeout=300)')
        self.assertEqual(outcome['status'], 'fail', outcome['summary'])

    def test_conflicting_nonblocking_rules_do_not_arbitrarily_skip(self):
        job = self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.guard_state(states={"表单":page.get_by_role("form",name="工作区"),"购物车":page.get_by_test_id("cart-ready")},
            outcomes={"表单":"continue","购物车":"skip"}, timeout_ms=800)
''')
        job['parameters']['base_url'] += '/states/ready'
        outcome = asyncio.run(execute(job))
        self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
        self.assertIn('规则冲突', outcome['summary'])
    def test_timeout_is_blocked(self):
        job=self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await page.get_by_role("button", name="Missing").click(timeout=300)
        await expect(page.locator("h1")).to_be_visible()
''')
        outcome=asyncio.run(execute(job))
        self.assertEqual(outcome['status'],'blocked',outcome['summary'])

    def test_diagnostic_captures_field_state_and_never_reports_pass(self):
        job=self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await page.get_by_label("Name").fill("Observed value")
        await expect(page.get_by_label("Name")).to_have_value("Observed value")
''')
        job['diagnostic_only']=True
        outcome=asyncio.run(execute(job))
        self.assertEqual(outcome['status'],'blocked')
        self.assertIn('不计为业务通过',outcome['summary'])
        observations=json.loads(outcome['diagnostics']['step_observations'])
        field=next(n for n in observations[0]['controls'] if n['tag']=='input')
        self.assertEqual(field['value'],'Observed value')
        self.assertIn('borderColor',field['styles'])

    def test_navigation_failure_is_blocked_with_real_error_and_screenshot(self):
        job = self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.observe_state(states={"页面":page.get_by_role("heading")}, timeout_ms=500)
''')
        job['parameters']['base_url'] += '/unavailable'
        outcome = asyncio.run(execute(job))
        self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
        self.assertEqual(outcome['steps'][0]['status'], 'blocked')
        self.assertIn('ERR_EMPTY_RESPONSE', outcome['steps'][0]['actual'])
        self.assertTrue(outcome['steps'][0]['screenshot'])
        self.assertIn('url', outcome['diagnostics'])
        self.assertNotIn('condition', outcome['steps'][0])

    def test_parameter_fstring_and_native_css_assertion(self):
        job = self.job('''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await page.locator(f"input[aria-label='{params['field']}']").fill("Replay")
        await expect(page.locator(f"{params.get('target')}")).to_have_css("opacity", "1")
        await expect(page.get_by_label("Name")).to_have_value("Replay")
        await expect(page.locator("body")).not_to_contain_text("Validation error", use_inner_text=True)
        await expect(page.locator(".city-panel")).to_have_count(2)
        await expect(page.locator(".city-panel").first).to_be_hidden()
        await expect(page.locator(".city-panel").last).to_have_css("opacity", "0")
''')
        job['parameters'].update(field='Name', target='h1')
        outcome = asyncio.run(execute(job))
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])

    def popup(self, query='', expected='北京', panel='#panel', option='option_role="listitem", option_name="北京"'):
        job = self.job(f'''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.choose_option(trigger=page.locator("#trigger"), panel=page.locator("{panel}"),
            value=page.locator("#value"), expected_value="{expected}", {option}, label="城市", timeout_ms=800)
        await record.expect_closed(panel=page.locator("#panel"), timeout_ms=800)
        await expect(page.locator("#value")).to_have_value("{expected}")
''')
        job['parameters']['base_url'] += '/popup' + query
        return asyncio.run(execute(job))

    def test_popup_scopes_duplicate_name_and_opens_transparent_panel(self):
        outcome = self.popup()
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])
        self.assertIn('点击入口打开', outcome['steps'][0]['actual'])

    def test_open_popup_is_not_toggled_closed(self):
        outcome = self.popup('?open')
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])
        self.assertIn('保留当前状态', outcome['steps'][0]['actual'])
        self.assertEqual(outcome['steps'][0]['recovery_count'], 0)

    def test_popup_closing_during_preparation_is_opened_once(self):
        outcome = self.popup('?closing')
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])
        self.assertIn('保留当前状态', outcome['steps'][0]['actual'])
        self.assertIn('点击入口打开面板', outcome['steps'][0]['actual'])
        self.assertEqual(outcome['steps'][0]['recovery_count'], 0)

    def test_closed_popup_recovers_once_and_keeps_evidence(self):
        outcome = self.popup('?recover')
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])
        self.assertEqual(outcome['steps'][0]['recovery_count'], 1)
        self.assertEqual(len(outcome['steps'][0]['recovery_screenshots']), 1)
        self.assertIn('第 1 次点击候选项超时', outcome['steps'][0]['actual'])

    def test_wrong_selection_fails_without_recovery(self):
        outcome = self.popup(expected='上海')
        self.assertEqual(outcome['status'], 'fail', outcome['summary'])
        self.assertEqual(outcome['steps'][0]['recovery_count'], 0)

    def test_recovery_stops_after_one_reopen(self):
        outcome = self.popup('?always-recover')
        self.assertEqual(outcome['status'], 'blocked', outcome['summary'])
        self.assertEqual(outcome['steps'][0]['recovery_count'], 1)
        self.assertIn('第 2 次点击候选项超时', outcome['steps'][0]['actual'])

    def test_ambiguous_panel_is_not_silently_picked(self):
        outcome = self.popup(panel='ul')
        self.assertEqual(outcome['status'], 'error', outcome['summary'])
        self.assertIn('匹配多个', outcome['summary'])

    def test_hidden_panel_duplicates_are_ignored_but_visible_ambiguity_is_rejected(self):
        from replay_actions import PopupActions
        async def check():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content('''<input id="city"><button id="open" onclick="document.querySelector('#active').hidden=false">Open</button>
<ul class="panel" hidden><li>Paris</li></ul>
<ul class="panel" id="active" hidden><li onclick="document.querySelector('#city').value='Paris';this.parentElement.hidden=true">Paris</li></ul>''')
                    actions = PopupActions()
                    await actions.choose_option(trigger=page.locator('#open'), panel=page.locator('.panel'),
                        value=page.locator('#city'), expected_value='Paris', option_selector='li')
                    self.assertEqual(await page.locator('#city').input_value(), 'Paris')
                    await page.locator('.panel').evaluate_all('els=>els.forEach(e=>e.hidden=false)')
                    with self.assertRaisesRegex(ValueError, '多个可见面板'):
                        await actions._panel_state(page.locator('.panel'))
                finally:
                    await browser.close()
        asyncio.run(check())

    def test_date_selection_uses_scoped_css(self):
        outcome = self.popup(expected='2026-10-22', option='option_selector="[data-date=\\"2026-10-22\\"]"')
        self.assertEqual(outcome['status'], 'pass', outcome['summary'])

    def test_runtime_helpers_match_backend_download(self):
        root = Path(__file__).resolve().parent
        self.assertEqual((root / 'replay_actions.py').read_bytes(),
                         (root.parent / 'Django/playwright_scripts/runtime_actions.py').read_bytes())


if __name__=='__main__': unittest.main()
