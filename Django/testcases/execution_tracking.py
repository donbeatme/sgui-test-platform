"""One execution record per attempt; unknown outcomes never count as passes."""
import hashlib
import json
import re
import uuid
from collections import Counter
from pathlib import PurePath

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import TestCase, TestCaseResult, TestExecution, TestCaseScreenshot

ACTIVE = ('pending', 'running')


def snapshot_case(case):
    data = {'id': case.pk, 'name': case.name, 'precondition': case.precondition,
            'steps': list(case.steps.order_by('step_number').values('step_number', 'description', 'expected_result'))}
    data['revision'] = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return data


@transaction.atomic
def begin_execution(project, user, case_id, session_id, result_id=None):
    if result_id:
        # Serialize suite starts and cancellation in the same parent-first order.
        parent_id = TestCaseResult.objects.filter(pk=result_id).values_list('execution_id', flat=True).first()
        if not parent_id or not TestExecution.objects.select_for_update().filter(
                pk=parent_id, executor=user, project=project, status__in=ACTIVE).exists():
            raise ValidationError('执行记录已结束或不属于当前用户和项目。')
    case = TestCase.objects.select_for_update().filter(pk=case_id, project=project).first()
    if not case:
        raise ValidationError('用例不存在或不属于当前项目。')
    if len(session_id) > 200:
        raise ValidationError('会话编号过长。')
    if TestCaseResult.objects.filter(mcp_session_id=session_id, status='running').exists():
        raise ValidationError('该会话已有执行中的用例，请先停止或使用新会话。')
    now = timezone.now()
    if result_id:
        result = TestCaseResult.objects.select_for_update().filter(
            Q(mcp_session_id='') | Q(mcp_session_id__isnull=True),
            pk=result_id, testcase=case, execution__executor=user,
            execution__project=project, execution__status__in=ACTIVE,
            status__in=ACTIVE).first()
        if not result:
            raise ValidationError('执行记录已结束、已启动或不属于当前用户和项目。')
    else:
        execution = TestExecution.objects.create(project=project, executor=user, source='ai_single',
            name=case.name, status='running', total_count=1, started_at=now)
        result = TestCaseResult.objects.create(execution=execution, testcase=case)
    result.case_snapshot = result.case_snapshot or snapshot_case(case)
    result.status, result.started_at, result.mcp_session_id = 'running', now, session_id
    result.evidence_token = uuid.uuid4().hex
    # Freeze known entry points without copying any login secrets into the report.
    from .preflight import urls_in, valid_url
    urls = urls_in((result.case_snapshot.get('precondition') or '') + '\n' + '\n'.join(s['description'] for s in result.case_snapshot.get('steps', [])))
    result.target_urls = list(dict.fromkeys(urls or list(project.credentials.exclude(system_url='').values_list('system_url', flat=True))))
    result.target_urls = [u for u in result.target_urls if valid_url(u)]
    result.save()
    return result


def execution_instruction(result_id):
    result = TestCaseResult.objects.get(pk=result_id)
    prefix = f'run{result.pk}-{result.evidence_token}-step'
    return '\n' + f'''本次正式执行记录 ID：{result.execution_id}，结果 ID：{result.pk}。
本次入口地址：{json.dumps(result.target_urls, ensure_ascii=False)}。无单条用例网址时沿用项目配置，不要自行猜测被测网站。
必须按以下冻结用例执行，预期结果不能根据实际页面改写：
{json.dumps(result.case_snapshot, ensure_ascii=False)}
截图必须使用此前缀：{prefix}，例如 {prefix}1.png；每步上传后读取返回的截图 id。
最终回答先说明结论，末尾必须输出一个 ```json 代码块，格式为：
{{"execution_result":{{"status":"pass|fail|blocked","summary":"结论及原因","steps":[{{"step_number":1,"status":"pass|fail|blocked|not_run","expected":"原步骤预期原文","actual":"实际观察到的值及比较依据，不能仅写已完成","screenshot_ids":[实际上传返回的截图整数ID]}}]}}}}
只有所有原步骤均有实际断言依据且本轮截图已上传，才能报告 pass。未执行/缺少证据应报告 blocked。
访问被拦截、验证码、工具不可用属于 blocked，不是业务断言失败。不得使用以前轮次的截图。
'''


def screenshot_result(case, filename, step_number):
    """Bind uploads by unguessable run prefix, not by all screenshots on a case."""
    name = PurePath(filename.replace('\\', '/')).name
    match = re.match(r'^run(\d+)-([a-f0-9]{32})-step', name)
    if not match:
        return None
    result = TestCaseResult.objects.filter(pk=match[1], evidence_token=match[2],
        testcase=case, status='running', execution__status__in=ACTIVE).first()
    if not result or step_number not in {s['step_number'] for s in result.case_snapshot.get('steps', [])}:
        raise ValidationError('截图对应的执行已结束或步骤编号不属于本轮用例。')
    return result


def parse_report(text):
    # Only a named JSON envelope from the final assistant message is accepted.
    candidates = re.findall(r'```(?:json)?\s*([\s\S]*?)```', text or '')
    candidates.append((text or '').strip())
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict) and isinstance(value.get('execution_result'), dict):
                return value['execution_result']
        except (ValueError, TypeError):
            pass
    return None


def judge_report(result, report):
    original = result.case_snapshot.get('steps', [])
    evidence = {s.pk: s for s in result.evidence.all()}
    conditional_replay = result.execution.source == 'playwright_script'
    # Written by the server when creating a run; LLM/client reports cannot opt out.
    basic_replay = conditional_replay and (result.model_info or {}).get('acceptance') == 'replay_once'
    if not isinstance(report, dict) or report.get('status') not in (('pass', 'fail', 'blocked', 'skip') if conditional_replay else ('pass', 'fail', 'blocked')) or not isinstance(report.get('steps'), list):
        return 'blocked', [], '缺少有效的结构化最终结果，未判为通过。'
    received = report['steps']
    known = {s['step_number'] for s in original}
    numbers = [s.get('step_number') if isinstance(s, dict) else None for s in received]
    if any(type(n) is not int or n not in known for n in numbers) or len(set(numbers)) != len(numbers):
        return 'blocked', [], '结果步骤编号重复或与本轮用例不匹配。'
    by_number = {s['step_number']: s for s in received}
    steps, invalid, failed = [], False, False
    for source in original:
        item = by_number.get(source['step_number'], {})
        status = item.get('status', 'not_run')
        actual, ids = item.get('actual', ''), item.get('screenshot_ids', [])
        reason = ''
        condition = item.get('condition', {})
        if status not in (('pass', 'fail', 'blocked', 'not_run', 'skip') if conditional_replay else ('pass', 'fail', 'blocked', 'not_run')):
            status, reason = 'blocked', '步骤状态无效'
        if status in ('pass', 'fail', 'skip'):
            if item.get('expected') != source['expected_result']:
                status, reason = 'blocked', '返回的预期与执行前冻结的预期不一致'
            elif not isinstance(actual, str) or not actual.strip():
                status, reason = 'blocked', '没有实际观察或断言依据'
            elif (not isinstance(ids, list) or (not ids and not basic_replay)
                  or any(type(i) is not int or i not in evidence or evidence[i].step_number != source['step_number'] for i in ids)):
                status, reason = 'blocked', '缺少本轮本步骤的有效截图'
            elif status == 'skip' and (not isinstance(condition, dict) or condition.get('outcome') != 'skip'
                    or not isinstance(condition.get('matched'), list) or not 1 <= len(condition['matched']) <= 8
                    or any(not isinstance(s, str) or not s.strip() for s in condition['matched'])
                    or not isinstance(condition.get('reason'), str) or not condition['reason'].strip()):
                status, reason = 'blocked', '未触发／不适用必须有实际状态判断依据，不能因未执行或缺少定位而跳过'
        valid_ids = [i for i in ids if type(i) is int and i in evidence and evidence[i].step_number == source['step_number']] if isinstance(ids, list) else []
        steps.append({**source, 'status': status, 'actual': actual if isinstance(actual, str) else '',
                      'reason': reason, 'screenshot_ids': valid_ids,
                      **({'condition': condition} if isinstance(condition, dict) and condition else {}),
                      'screenshots': [evidence[i].screenshot.url for i in valid_ids]})
        invalid = invalid or status in ('blocked', 'not_run')
        failed = failed or status == 'fail'
    # Deterministic replay stops at its first failed assertion. Remaining not-run
    # steps do not erase that observed failure, but missing evidence still blocks.
    script_assertion_failure = (result.execution.source == 'playwright_script' and failed
        and report['status'] == 'fail' and not any(s['status'] == 'blocked' for s in steps))
    skip_index = next((i for i, s in enumerate(steps) if s['status'] == 'skip'), None)
    valid_conditional_skip = (conditional_replay and report['status'] == 'skip' and skip_index is not None
        and all(s['status'] == 'pass' for s in steps[:skip_index])
        and all(s['status'] == 'not_run' for s in steps[skip_index + 1:]))
    if valid_conditional_skip:
        status = 'skip'
        for step in steps[skip_index + 1:]:
            step['reason'] = '本用例条件未触发，后续步骤不适用。'
    elif script_assertion_failure:
        status = 'fail'
    elif not original or invalid or report['status'] in ('blocked', 'skip') or skip_index is not None:
        status = 'blocked'
    elif failed:
        status = 'fail'
    elif report['status'] == 'pass':
        status = 'pass'
    else:
        status = 'blocked'
    summary = str(report.get('summary') or '')[:4000]
    if status == 'blocked':
        summary = '执行未完整验证或结果不一致，未计入通过。' + summary
    return status, steps, summary


@transaction.atomic
def refresh_execution(execution_id):
    execution = TestExecution.objects.select_for_update().get(pk=execution_id)
    counts = Counter(execution.results.values_list('status', flat=True))
    for state, field in [('pass', 'passed_count'), ('fail', 'failed_count'), ('error', 'error_count'),
                         ('skip', 'skipped_count'), ('blocked', 'blocked_count'), ('cancelled', 'cancelled_count')]:
        setattr(execution, field, counts[state])
    if counts['pending'] or counts['running'] or sum(counts.values()) < execution.total_count:
        execution.status = 'running' if execution.status != 'cancelled' else 'cancelled'
    else:
        execution.status = ('cancelled' if execution.status == 'cancelled' or counts['cancelled'] else
                            'error' if counts['error'] else 'failed' if counts['fail'] else
                            'blocked' if counts['blocked'] else
                            'skipped' if counts['skip'] and counts['skip'] == sum(counts.values()) else 'completed')
        execution.completed_at = execution.completed_at or timezone.now()
    execution.save()
    return execution


@transaction.atomic
def finish_execution(result_id, report=None, forced=None, reason='', log=''):
    # All paths lock parent first to serialize cancellation and result finalization.
    execution_id = TestCaseResult.objects.values_list('execution_id', flat=True).get(pk=result_id)
    TestExecution.objects.select_for_update().get(pk=execution_id)
    result = TestCaseResult.objects.select_for_update().get(pk=result_id)
    if result.status not in ACTIVE:
        # A late stream may append diagnostic evidence, never change the outcome.
        if log:
            result.execution_log = (log[-60000:] + '\n\n' + (result.execution_log or '')[-4000:])
            result.screenshots = [s.screenshot.url for s in result.evidence.order_by('step_number', 'id')]
            result.save(update_fields=['execution_log', 'screenshots', 'updated_at'])
        return result
    status, steps, summary = judge_report(result, report)
    if forced in ('cancelled', 'error', 'blocked'):
        status, summary = forced, reason or summary
    result.status, result.step_results = status, steps
    result.error_message = summary if status != 'pass' else ''
    result.completed_at = timezone.now()
    result.execution_time = (result.completed_at - result.started_at).total_seconds() if result.started_at else 0
    result.screenshots = [s.screenshot.url for s in result.evidence.order_by('step_number', 'id')]
    result.execution_log = (log[-60000:] + '\n\n判定：' + status + '\n' + summary).strip()
    result.save()
    refresh_execution(result.execution_id)
    # Script generation cannot change this attempt's result or inflate statistics.
    from playwright_scripts.services import finalize_capture
    transaction.on_commit(lambda: finalize_capture(result_id))
    return result


@transaction.atomic
def cancel_execution(execution_id):
    from orchestrator_integration.stop_signal import set_stop_signal
    execution = TestExecution.objects.select_for_update().get(pk=execution_id)
    if execution.status not in ACTIVE:
        return
    for result in execution.results.filter(status__in=ACTIVE):
        if result.mcp_session_id:
            set_stop_signal(result.mcp_session_id)
        finish_execution(result.pk, forced='cancelled', reason='用户取消了本次执行。')
    execution.refresh_from_db()
    execution.status, execution.completed_at = 'cancelled', timezone.now()
    execution.save(update_fields=['status', 'completed_at', 'updated_at'])
