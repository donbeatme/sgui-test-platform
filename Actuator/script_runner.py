"""Run one server-validated replay artifact; never call an LLM."""
import asyncio
import base64
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from playwright.async_api import async_playwright, expect, TimeoutError as PlaywrightTimeout, Error as PlaywrightError
from replay_actions import PopupActions, ReplayStopped, ReplayNotApplicable
from page_evidence import capture_page_evidence
from date_parameters import resolve_parameters


class Recorder(PopupActions):
    def __init__(self, page, snapshot, diagnostic_only=False, require_screenshots=True):
        self.page = page
        self.expected = {s['step_number']: s for s in snapshot['steps']}
        self.steps = []
        self.current = None
        self.diagnostics = {}
        self.diagnostic_only = diagnostic_only
        self.require_screenshots = require_screenshots
        self.observations = []

    async def capture_diagnostics(self, number):
        """Bounded read-only evidence for the next generation, before closing the page."""
        try:
            self.diagnostics = {**await capture_page_evidence(self.page), 'step_number': number}
        except Exception:
            self.diagnostics['note'] = '未能完整获取失败页面的只读快照，见逐步截图。'

    async def _action_event(self, message):
        if self.current is None:
            raise ValueError('公共操作必须位于 record.step 内。')
        self.current['actions'].append(message)

    async def _state_decision(self, decision):
        if self.current is None:
            raise ValueError('状态判断必须位于 record.step 内。')
        self.current['condition'] = decision

    async def _recovery_evidence(self, message):
        await self._action_event(message)
        self.current['recovery_count'] += 1
        if len(self.current['recovery_screenshots']) >= 4:
            await self._action_event('本步已保留 4 张恢复前截图，后续恢复仍记录原因。')
            return
        try:
            shot = await self.page.screenshot(full_page=False, timeout=5000)
            self.current['recovery_screenshots'].append(base64.b64encode(shot).decode())
        except Exception:
            await self._action_event('恢复前截图未能保存，首次失败原因已保留。')

    @asynccontextmanager
    async def step(self, number):
        if number not in self.expected or number in [s['step_number'] for s in self.steps]:
            raise ValueError('步骤编号重复或不属于本次用例。')
        item = {'step_number': number, 'status': 'pass', 'actual': '',
                'actions': [], 'recovery_count': 0, 'recovery_screenshots': []}
        self.current = item
        try:
            yield
            item['actual'] = '本步骤检查已完成。页面：' + self.page.url + '；标题：' + await self.page.title()
        except ReplayNotApplicable as exc:
            item['status'], item['actual'] = 'skip', str(exc)[:4000]
            raise
        except ReplayStopped as exc:
            item['status'], item['actual'] = 'blocked', str(exc)[:4000]
            raise
        except AssertionError as exc:
            item['status'], item['actual'] = 'fail', str(exc)[:4000]
            raise
        except BaseException as exc:
            item['status'], item['actual'] = 'blocked', type(exc).__name__ + ': ' + str(exc)[:4000]
            raise
        finally:
            if item['actions']:
                item['actual'] += '\n操作过程（恢复次数：' + str(item['recovery_count']) + '）：\n' + '\n'.join(item['actions'])
            try:
                picture = await self.page.screenshot(full_page=False, timeout=5000)
                item['screenshot'] = base64.b64encode(picture).decode()
            except Exception:
                if self.require_screenshots:
                    item['status'] = 'blocked'
                item['actual'] += '；本步截图保存失败。'
            if item['status'] != 'pass':
                await self.capture_diagnostics(number)
            if self.diagnostic_only and len(self.observations)<6:
                evidence=await capture_page_evidence(self.page)
                try:
                    dom=json.loads(evidence.get('dom','{}'))
                    observation={'step_number':number,'url':self.page.url,'controls':[
                        n for n in dom.get('nodes',[]) if n.get('visible') and
                        (n.get('tag') in ('input','select','textarea') or n.get('styles') or
                         n.get('attrs',{}).get('role')=='alert')]}
                    while len(json.dumps(observation,ensure_ascii=False))>5000 and observation['controls']:
                        observation['controls'].pop()
                        observation['truncated']=True
                    self.observations.append(observation)
                except (ValueError,TypeError):
                    pass
            self.steps.append(item)
            self.current = None


async def execute(job):
    parameters = dict(job['parameters'])
    for key, value in os.environ.items():
        if key.startswith('SGUI_PARAM_'):
            parameters[key[len('SGUI_PARAM_'):]] = value
    parameters = resolve_parameters(parameters)
    record = None
    basic = job.get('acceptance') == 'replay_once'
    status, summary, log = 'pass', ('脚本复跑通过：所有步骤执行完成，无执行错误。' if basic else '全部脚本步骤与断言通过。'), ''
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=job.get('headless', False))
            try:
                # Separate context for each run: no personal browser profile or implicit login copy.
                context = await browser.new_context(viewport={'width': 1440, 'height': 900})
                page = await context.new_page()
                page.set_default_timeout(15000)
                page.set_default_navigation_timeout(45000)
                record = Recorder(page, job['snapshot'], job.get('diagnostic_only') is True,
                                  require_screenshots=not basic)
                scope = {'__builtins__': {}, 'expect': expect}
                exec(compile(job['code'], '<saved-playwright-script>', 'exec'), scope)
                await asyncio.wait_for(scope['run'](page, parameters, record), timeout=job.get('timeout_seconds', 600))
                if [s['step_number'] for s in record.steps] != list(record.expected) or any(s['status'] != 'pass' for s in record.steps):
                    status, summary = 'blocked', ('脚本没有完整执行全部步骤。' if basic else '脚本没有完成全部步骤或截图。')
            finally:
                await browser.close()
    except ReplayNotApplicable as exc:
        status, summary = 'skip', str(exc)[:3000]
    except ReplayStopped as exc:
        status, summary = 'blocked', '已记录状态并停止：' + str(exc)[:3000]
    except AssertionError as exc:
        status, summary = 'fail', 'Playwright 断言不符合预期：' + str(exc)[:3000]
    except (TimeoutError, PlaywrightTimeout) as exc:
        status, summary = 'blocked', '页面操作或脚本执行超时：' + str(exc)[:3000]
    except PlaywrightError as exc:
        status, summary = 'blocked', '浏览器操作未完成，已保留当前步骤现场：' + str(exc)[:3000]
    except Exception as exc:
        status, summary = 'error', type(exc).__name__ + ': ' + str(exc)[:3000]
    steps = record.steps if record else []
    diagnostics = record.diagnostics if record else {}
    if record and record.observations:
        diagnostics['step_observations']=json.dumps(record.observations,ensure_ascii=False)
    if job.get('diagnostic_only') is True and status not in ('cancelled','error'):
        status, summary = 'blocked', '诊断采集已结束，不计为业务通过。'+summary
    recoveries = sum(s.get('recovery_count', 0) for s in steps)
    if status == 'pass' and recoveries:
        summary += f' 共发生 {recoveries} 次面板恢复；详情见逐步操作记录。'
    log = '\n'.join(f"步骤 {s['step_number']}：{s['status']} · {s['actual']}" for s in steps)
    # Strip environment-supplied secrets from results; never log parameters.
    for key, value in parameters.items():
        if any(word in key.lower() for word in ('password', 'secret', 'token', 'cookie')) and value:
            secret = str(value)
            summary, log = summary.replace(secret, '[REDACTED]'), log.replace(secret, '[REDACTED]')
            for step in steps:
                step['actual'] = step['actual'].replace(secret, '[REDACTED]')
                if 'condition' in step:
                    decision = step['condition']
                    decision['matched'] = [s.replace(secret, '[REDACTED]') for s in decision['matched']]
                    decision['reason'] = decision['reason'].replace(secret, '[REDACTED]')
            diagnostics = {k: v.replace(secret, '[REDACTED]') if isinstance(v, str) else v
                           for k, v in diagnostics.items()}
    for step in steps:
        # Events are already included in the redacted actual/log text.
        step.pop('actions', None)
    return {'status': status, 'summary': summary, 'steps': steps, 'log': log, 'diagnostics': diagnostics}


if __name__ == '__main__':
    job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    outcome = asyncio.run(execute(job))
    Path(sys.argv[2]).write_text(json.dumps(outcome, ensure_ascii=False), encoding='utf-8')
