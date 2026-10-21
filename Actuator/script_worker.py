"""Poll a small authenticated replay queue alongside the existing UI actuator."""
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
import httpx
from runtime_env import should_force_headless

logger = logging.getLogger('actuator')


class ScriptWorker:
    def __init__(self, consumer):
        self.consumer = consumer
        self.worker_id = consumer.ws_client.actuator_id

    async def post(self, client, operation, data):
        for _ in range(2):
            token = await self.consumer._get_api_token()
            if not token:
                raise RuntimeError('执行器未认证。')
            response = await client.post(self.consumer.api_base_url + '/api/playwright-worker/' + operation + '/',
                headers={'Authorization': 'Bearer ' + token}, json={'worker_id': self.worker_id, **data})
            if response.status_code == 401:
                self.consumer._api_token = None
                continue
            response.raise_for_status()
            value = response.json()
            return value.get('data', value)
        raise RuntimeError('执行器认证已过期。')

    async def execute(self, client, job):
        runner = Path(__file__).with_name({'observe': 'script_probe.py', 'guided': 'script_guided.py'}.get(job.get('kind'), 'script_runner.py'))
        # Runtime files follow the configured D-drive TEMP, never a personal browser profile.
        with tempfile.TemporaryDirectory(prefix='sgui-replay-') as folder:
            source, result = Path(folder) / 'job.json', Path(folder) / 'result.json'
            source.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
            process = await asyncio.create_subprocess_exec(sys.executable, str(runner), str(source), str(result),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **options)
            identity = {'run_id': job['id'], 'lease_token': job['lease_token']}
            if job.get('kind') in ('observe', 'guided'):
                identity['kind'] = job['kind']
            answered_seq = -1
            started = asyncio.get_running_loop().time()
            failures = 0
            forced = None
            try:
                while process.returncode is None:
                    try:
                        heartbeat = await self.post(client, 'heartbeat', identity)
                        if heartbeat.get('cancelled'):
                            forced = 'cancelled'
                            break
                        if job.get('kind') == 'guided':
                            request_file = Path(folder) / 'request.json'
                            if request_file.exists():
                                request_data = json.loads(request_file.read_text(encoding='utf-8'))
                                if request_data['seq'] > answered_seq:
                                    answer = await self.post(client, 'next', {**identity, **request_data})
                                    if not answer.get('pending'):
                                        temporary = Path(folder) / 'answer.tmp'
                                        temporary.write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')
                                        temporary.replace(Path(folder) / f"answer-{request_data['seq']}.json")
                                        answered_seq = request_data['seq']
                        failures = 0
                    except Exception:
                        failures += 1
                        if failures >= 3:
                            forced = 'error'
                            break
                    if self.consumer._stop_event.is_set():
                        forced = 'cancelled'
                        break
                    if asyncio.get_running_loop().time() - started > job['timeout_seconds'] + 30:
                        forced = 'blocked'
                        break
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        pass
            finally:
                if process.returncode is None:
                    # This PID is the child we just created, never another user's browser.
                    if os.name == 'nt':
                        killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F',
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **options)
                        await killer.wait()
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                    await process.wait()
            if forced:
                outcome = {'status': forced, 'summary': '用户停止、执行超时或执行器连接中断。', 'steps': []}
            elif result.exists():
                outcome = json.loads(result.read_text(encoding='utf-8'))
            else:
                outcome = {'status': 'error', 'summary': '脚本运行器异常退出，未生成报告。', 'steps': []}
            for attempt in range(3):
                try:
                    await self.post(client, 'complete', {**identity, **outcome})
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception('Script result upload failed, run %s', job['id'])
                    else:
                        await asyncio.sleep(2)

    async def run(self):
        async with httpx.AsyncClient(timeout=20) as client:
            while not self.consumer._stop_event.is_set():
                try:
                    result = await self.post(client, 'claim', {'supports_headed': not should_force_headless(), 'runtime_version': 9})
                    if result.get('job'):
                        await self.execute(client, result['job'])
                except Exception as exc:
                    logger.debug('Replay queue unavailable: %s', type(exc).__name__)
                await asyncio.sleep(3)
