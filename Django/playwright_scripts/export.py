import json
from pathlib import Path
from .acceptance import basic_version


def standalone(version):
    """A single downloadable .py, with native assertions and a small CLI harness."""
    header = '''# Playwright Python replay. Install: pip install playwright; playwright install chromium
# Run: python case.py --base-url https://your-site.example --params '{"search":"value"}'
# Credentials: SGUI_PARAM_password etc. Never embed real credentials in this file.
import argparse, asyncio, json, os
from pathlib import Path
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, expect

'''
    # Embed the same trusted helpers used by the actuator, so downloads remain standalone.
    helpers = Path(__file__).with_name('runtime_actions.py').read_text(encoding='utf-8')
    helpers += '\n' + Path(__file__).with_name('date_parameters.py').read_text(encoding='utf-8')
    recorder = '''
class Recorder(PopupActions):
    async def _action_event(self, message):
        print(message)

    async def _recovery_evidence(self, message):
        print(message)
        self.recoveries += 1
        await self._save_screenshot(f"step-{self.number}-recovery-{self.recoveries}.png")

    async def _save_screenshot(self, path):
        try:
            await self.page.screenshot(path=path, timeout=5000)
        except Exception as exc:
            if REQUIRE_SCREENSHOTS:
                raise
            print(f"Screenshot unavailable: {exc}")

    @asynccontextmanager
    async def step(self, number):
        self.number, self.recoveries = number, 0
        try:
            yield
            print(f"Step {number}: PASS")
        except ReplayNotApplicable as exc:
            print(f"Step {number}: NOT APPLICABLE — {exc}")
            raise
        except BaseException:
            print(f"Step {number}: NOT PASSED")
            raise
        finally:
            await self._save_screenshot(f"step-{number}.png")

'''
    footer = '''
async def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--base-url')
    cli.add_argument('--params', default='{}')
    cli.add_argument('--headless', action='store_true')
    args = cli.parse_args()
    params = dict(DEFAULT_PARAMETERS)
    params.update(json.loads(args.params))
    if args.base_url:
        params['base_url'] = args.base_url
    params.update({k[len('SGUI_PARAM_'):]: v for k, v in os.environ.items() if k.startswith('SGUI_PARAM_')})
    params = resolve_parameters(params)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            page.set_default_timeout(15000)
            record = Recorder()
            record.page = page
            await run(page, params, record)
        finally:
            await browser.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except ReplayNotApplicable as exc:
        print(f"NOT APPLICABLE: {exc}")
        raise SystemExit(2)
    except ReplayStopped as exc:
        print(f"BLOCKED: {exc}")
        raise SystemExit(3)
'''
    return (header + helpers + '\n' + recorder + 'REQUIRE_SCREENSHOTS = ' + repr(not basic_version(version)) + '\n'
            + 'DEFAULT_PARAMETERS = json.loads(' + repr(json.dumps(version.parameters, ensure_ascii=False)) + ')\n\n'
            + version.code + '\n' + footer)
