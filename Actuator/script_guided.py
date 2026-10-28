"""A persistent browser learning session; server-validated fragments only.

The parent worker owns HTTP authentication, leasing, cancellation and model
requests. Child/parent messages are private temporary files, never browser data.
"""
import asyncio
import ast
import re
import json
import sys
from pathlib import Path
from playwright.async_api import async_playwright, expect
from replay_actions import PopupActions, prepare_browser_context
from page_evidence import capture_page_evidence
from date_parameters import resolve_parameters


class SessionRecorder(PopupActions):
    def __init__(self, page):
        self.page = page

    async def _action_event(self, message):
        pass

    async def _recovery_evidence(self, message):
        pass

    async def _state_decision(self, decision):
        pass


async def exchange(folder, request):
    target = folder / 'request.json'
    temporary = folder / 'request.tmp'
    temporary.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
    answer = folder / f"answer-{request['seq']}.json"
    for _ in range(1500):
        if answer.exists():
            return json.loads(answer.read_text(encoding='utf-8'))
        await asyncio.sleep(.2)
    raise TimeoutError('生成服务未及时返回下一步操作。')


async def execute(job, folder):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=job.get('headless', False))
        try:
            context = await browser.new_context(viewport={'width': 1440, 'height': 900},
                reduced_motion='reduce' if job.get('reduce_motion') else 'no-preference')
            if job.get('reduce_motion'):
                await prepare_browser_context(context)
            page = await context.new_page()
            page.set_default_timeout(10000)
            page.set_default_navigation_timeout(45000)
            response = await page.goto(job['base_url'], wait_until='domcontentloaded')
            if response and response.status >= 400:
                return {'status': 'blocked', 'summary': f'目标入口返回 HTTP {response.status}，未能进行生成。'}
            await page.locator('body').wait_for(state='visible')
            try:
                await page.wait_for_load_state('load', timeout=8000)
            except Exception:
                pass
            record = SessionRecorder(page)
            # Keep local bindings between successful statements/batches, just as
            # the exported async function does. Top-level await preserves scope.
            scope = {'__builtins__': {}, 'expect': expect, 're': re, 'range': range,
                     'len': len, 'str': str, 'int': int, 'float': float, 'bool': bool,
                     'min': min, 'max': max, 'page': page, 'record': record}
            outcomes = []
            initial_visible = None
            for seq in range(60):
                evidence = await capture_page_evidence(page, known_visible=initial_visible)
                if initial_visible is None:
                    try:
                        initial_visible = [n['css'] for n in json.loads(evidence.get('dom', '{}')).get('nodes', []) if n.get('visible')]
                    except (ValueError, KeyError, TypeError):
                        initial_visible = []
                response = await exchange(folder, {'seq': seq, 'outcomes': outcomes,
                    'evidence': evidence})
                if response.get('finished'):
                    return {'status': 'pass', 'summary': '操作采集完成，等待独立复跑验证。'}
                if response.get('stop'):
                    return {'status': 'blocked', 'summary': response['stop']}
                command = response['command']
                outcomes = []
                for statement in command['statements']:
                    try:
                        scope['params'] = resolve_parameters(command['parameters'])
                        code = compile(statement, '<validated-generation-action>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                        result = eval(code, scope)
                        if code.co_flags & 0x80:
                            await result
                        outcomes.append({'ok': True})
                    except Exception as exc:
                        outcomes.append({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:2000]})
                        break
            return {'status': 'blocked', 'summary': '已达到后台操作上限，未交付未验证脚本。'}
        finally:
            await browser.close()


if __name__ == '__main__':
    source, result = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        output = asyncio.run(execute(json.loads(source.read_text(encoding='utf-8')), source.parent))
    except Exception as exc:
        output = {'status': 'blocked', 'summary': type(exc).__name__ + ': ' + str(exc)[:2000]}
    result.write_text(json.dumps(output, ensure_ascii=False), encoding='utf-8')
