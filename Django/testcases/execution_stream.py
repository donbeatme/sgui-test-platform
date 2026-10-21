"""Persist outcomes around SSE, including early failures and disconnects."""
import asyncio
import json
from functools import wraps
from asgiref.sync import sync_to_async
from .execution_tracking import finish_execution, parse_report


def track_case_stream(function):
    @wraps(function)
    async def wrapped(self, request, *args, **kwargs):
        result_id = getattr(request, 'execution_result_id', None)
        if not result_id:
            async for chunk in function(self, request, *args, **kwargs):
                yield chunk
            return
        preflight = getattr(request, 'execution_preflight', None)
        if preflight and not preflight['ready']:
            reason = '执行前检查未通过：' + '；'.join(e['message'] for e in preflight['errors'])
            result = await sync_to_async(finish_execution)(result_id, forced='blocked', reason=reason)
            for event in [
                {'type': 'stream', 'data': reason},
                {'type': 'execution_result', 'execution_id': result.execution_id, 'result_id': result.pk, 'status': result.status},
                {'type': 'complete', 'execution_status': result.status}]:
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
            yield 'data: [DONE]\n\n'
            return
        report, forced, reason, complete = None, None, '', False
        logs = []
        terminal_events = []
        generator = function(self, request, *args, **kwargs)
        try:
            async with asyncio.timeout(1800):
                async for chunk in generator:
                    if isinstance(chunk, str) and chunk.startswith('data: '):
                        raw = chunk[6:].strip()
                        if raw == '[DONE]':
                            continue
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            event = {}
                        kind = event.get('type')
                        if kind == 'case_final_response':
                            report = parse_report(event.get('text', ''))
                            logs.append('AI 最终报告：\n' + event.get('text', '')[-24000:])
                            continue
                        if kind == 'error':
                            forced, reason = 'error', str(event.get('message', '执行服务异常'))
                        elif kind == 'stopped' or (kind == 'complete' and event.get('status') == 'stopped'):
                            forced, reason = 'cancelled', '用户停止了本次执行。'
                        elif kind == 'interrupt':
                            forced, reason = 'blocked', '执行等待人工审批，尚未完成验证。审批后请核查并重新执行。'
                        elif kind == 'complete':
                            complete = True
                        elif kind in ('tool_start', 'tool_result', 'step_start', 'warning'):
                            logs.append(f"{kind}: {event.get('tool_name') or event.get('name') or event.get('step') or event.get('message') or ''}")
                            if kind == 'tool_result':
                                from orchestrator_integration.execution_guard import tool_error_detail
                                detail = tool_error_detail(event.get('tool_output', ''))
                                if detail:
                                    logs.append('工具错误详情：' + detail)
                        if kind in ('complete', 'error', 'stopped', 'interrupt'):
                            # Persist before the client observes a terminal event and closes SSE.
                            terminal_events.append(chunk)
                            continue
                    yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            await asyncio.shield(sync_to_async(finish_execution)(result_id, report,
                forced='cancelled', reason='执行连接已关闭，本次执行未完成。', log='\n'.join(logs)))
            raise
        except Exception as exc:
            forced = 'blocked' if isinstance(exc, TimeoutError) else 'error'
            reason = '执行超时（30 分钟），未完成验证。' if isinstance(exc, TimeoutError) else f'执行服务异常：{type(exc).__name__}'
        finally:
            try:
                await generator.aclose()
            except Exception:
                forced, reason = 'error', '执行流关闭异常，结果未完整验证。'
        if not complete and not forced:
            forced, reason = 'blocked', '执行流未正常完成，缺少完整结果。'
        result = await sync_to_async(finish_execution)(result_id, report, forced=forced,
            reason=reason, log='\n'.join(logs))
        yield 'data: ' + json.dumps({'type': 'execution_result', 'execution_id': result.execution_id,
            'result_id': result.pk, 'status': result.status}, ensure_ascii=False) + '\n\n'
        for chunk in terminal_events:
            yield chunk
        if not terminal_events:
            yield 'data: ' + json.dumps({'type': 'complete', 'execution_status': result.status}) + '\n\n'
        yield 'data: [DONE]\n\n'
    return wrapped
