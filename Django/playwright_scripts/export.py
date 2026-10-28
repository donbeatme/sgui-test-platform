import json
import io
import zipfile
from pathlib import Path
from .acceptance import basic_version, reduced_motion_version


def standalone(version):
    """A single downloadable .py, with native assertions and a small CLI harness."""
    header = '''# Playwright Python replay. Install: pip install playwright; playwright install chromium
# Run: python case.py --base-url https://your-site.example --params '{"search":"value"}'
# Credentials: SGUI_PARAM_password etc. Never embed real credentials in this file.
import argparse, asyncio, json, os, re
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
            await self.page.screenshot(path=str(self.output_dir / path), timeout=5000)
        except Exception as exc:
            if REQUIRE_SCREENSHOTS:
                raise
            print(f"Screenshot unavailable: {exc}")

    @asynccontextmanager
    async def step(self, number):
        self.number, self.recoveries = number, 0
        item = {'step': number, 'status': 'pass'}
        try:
            yield
            print(f"Step {number}: PASS")
        except ReplayNotApplicable as exc:
            print(f"Step {number}: NOT APPLICABLE — {exc}")
            raise
        except BaseException as exc:
            item.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            print(f"Step {number}: NOT PASSED")
            raise
        finally:
            self.results.append(item)
            await self._save_screenshot(f"step-{number}.png")

'''
    footer = '''
async def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--base-url')
    cli.add_argument('--params', default='{}')
    cli.add_argument('--headless', action='store_true')
    cli.add_argument('--output', default='playwright-results')
    cli.add_argument('--timeout', type=int, default=180, help='Overall execution timeout in seconds')
    cli.add_argument('--native-motion', action='store_true', help='Use the original website animation timing')
    args = cli.parse_args()
    params = dict(DEFAULT_PARAMETERS)
    params.update(json.loads(args.params))
    if args.base_url:
        params['base_url'] = args.base_url
    params.update({k[len('SGUI_PARAM_'):]: v for k, v in os.environ.items() if k.startswith('SGUI_PARAM_')})
    params = resolve_parameters(params)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        try:
            context = await browser.new_context(viewport={'width': 1440, 'height': 900},
                reduced_motion='reduce' if REDUCE_MOTION and not args.native_motion else 'no-preference')
            if REDUCE_MOTION and not args.native_motion:
                await prepare_browser_context(context)
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = await context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(45000)
            record = Recorder()
            record.page = page
            record.output_dir, record.results = output_dir, []
            status = 'failed'
            try:
                await asyncio.wait_for(run(page, params, record), timeout=args.timeout)
                status = 'pass'
            finally:
                (output_dir / 'result.json').write_text(json.dumps({'status': status, 'steps': record.results}, ensure_ascii=False, indent=2), encoding='utf-8')
                await context.tracing.stop(path=str(output_dir / 'trace.zip'))
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
            + 'REDUCE_MOTION = ' + repr(reduced_motion_version(version)) + '\n'
            + 'DEFAULT_PARAMETERS = json.loads(' + repr(json.dumps(version.parameters, ensure_ascii=False)) + ')\n\n'
            + version.code + '\n' + footer)


def bundle(version):
    """Portable source bundle: no server, model key or platform imports required."""
    output = io.BytesIO()
    readme = '''# Playwright 可复用脚本

这是从真实页面操作生成、在平台独立复跑通过后导出的 Python 脚本。
运行时不依赖 SGUI 后端、数据库或 LLM，目标网站和测试数据仍需可用。

## 安装

需要 Python 3.10 或以上版本。进入解压目录，在终端执行：

```shell
python -m venv .venv
# Windows PowerShell
.\\.venv\\Scripts\\Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Linux 如缺少浏览器系统库，由管理员执行 `python -m playwright install-deps chromium`。
离线部署需要提前准备相应平台的 Python 依赖和匹配的 Chromium 浏览器。

## 运行

```shell
python case.py
python case.py --headless --output results/run-2
python case.py --base-url https://目标网站 --params '{"origin":"新的测试数据"}'
```

默认网址、参数在 `case.py` 的 DEFAULT_PARAMETERS 中；相对日期每次按运行日计算。
默认显示浏览器。`--timeout 180` 设置整条用例超时秒数。
新版本默认缩短视觉动画，与平台生成、复跑环境一致；`--native-motion` 可恢复原始动画时序，恢复后需要重新验证。
凭据通过环境变量 `SGUI_PARAM_password` 等传入，不要写进脚本或分享包。

结果保存到 `playwright-results/`，包含 `result.json`、逐步截图和 `trace.zip`。
成功退出码为 0；断言失败、执行中断返回非零。查看轨迹：

```shell
python -m playwright show-trace playwright-results/trace.zip
```

源用例见 `case.json`。修改参数后请重新运行确认；网站结构变化可能需要更新定位器。
平台复跑通过表示该版本当次可执行，不保证所有业务条件或后续网站版本始终通过。
'''
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('case.py', standalone(version))
        archive.writestr('requirements.txt', 'playwright==1.61.0\n')
        archive.writestr('README.md', readme)
        archive.writestr('case.json', json.dumps(version.script.snapshot, ensure_ascii=False, indent=2))
    return output.getvalue()
