import json
import logging
import re
import uuid
from django.db import transaction
from django.utils import timezone
from .models import SavedScript, ScriptVersion
from .contract import validate, normalize
from .review import separate_notes

logger = logging.getLogger(__name__)


def enqueue_generation(script_id, token):
    """A broker failure must leave a visible, retryable task rather than a stuck queue."""
    from .tasks import generate_script
    try:
        generate_script.delay(script_id, token)
    except Exception:
        logger.exception('Cannot queue script generation %s', script_id)
        SavedScript.objects.filter(pk=script_id, generation_token=token).update(
            generation_status='error', generation_error='生成任务未能提交，请启动后台任务服务后重新生成。',
            updated_at=timezone.now())
        from .models import ScriptBuild
        ScriptBuild.objects.filter(script_id=script_id, generation_token=token, status='queued').update(
            status='error', error='生成任务未能入队。', updated_at=timezone.now())


@transaction.atomic
def generate_from_case(project, user, case_id, base_url=None, build_options=None):
    from rest_framework.exceptions import ValidationError, NotFound
    from testcases.models import TestCase
    from testcases.execution_tracking import snapshot_case
    from testcases.preflight import urls_in, valid_url
    from langgraph_integration.models import LLMConfig

    # Lock the case to deduplicate double clicks across tabs/users.
    case = TestCase.objects.select_for_update().filter(pk=case_id, project=project).first()
    if not case:
        raise NotFound('用例不存在或不属于当前项目。')
    if case.review_status == 'unavailable':
        raise ValidationError('该用例已标记不可用，请先恢复可用状态。')
    snapshot = snapshot_case(case)
    if not snapshot['steps'] or any(not (s['description'] or '').strip() or
            not (s['expected_result'] or '').strip() for s in snapshot['steps']):
        raise ValidationError('请先补全用例的操作步骤和每步预期结果。')
    explicit = urls_in((case.precondition or '') + '\n' + '\n'.join(s['description'] for s in snapshot['steps']))
    configured = list(dict.fromkeys(c.system_url.strip().rstrip('/') for c in project.credentials.all() if c.system_url.strip()))
    urls = [base_url] if base_url is not None else (explicit or configured)
    if not urls:
        raise ValidationError('请先在项目管理的系统凭据中配置被测网站地址，或在用例前置条件中填写入口网址。')
    if len(urls) != 1:
        raise ValidationError('发现多个入口地址，请在用例前置条件中明确唯一入口后生成脚本。')
    if not isinstance(urls[0], str) or not valid_url(urls[0]):
        raise ValidationError('入口须为有效 HTTP/HTTPS 地址，且不能包含账号密码。')
    if redact(urls[0], project) != urls[0]:
        raise ValidationError('入口网址含有敏感参数，请改用不包含凭据的入口。')
    model = LLMConfig.objects.filter(is_active=True).first()
    if not model or not model.name or not valid_url(model.api_url):
        raise ValidationError('请先在模型配置中启用可用的 LLM。')
    active = SavedScript.objects.filter(project=project, testcase=case, deleted=False,
        generation_mode='case', generation_status__in=['queued', 'generating', 'observing', 'validating'],
        snapshot__revision=snapshot['revision'], generation_context__base_url=urls[0]).first()
    if active and (timezone.now() - active.updated_at).total_seconds() < (1800 if active.builds.exists() else 600):
        return active, False
    if active:
        active.generation_status, active.generation_error = 'error', '上次生成超时，已创建新的生成任务。'
        active.generation_token = uuid.uuid4()
        active.save()
    context = {'base_url': urls[0], 'url_source': 'override' if base_url is not None else ('case' if explicit else 'project'),
               'model_name': model.config_name}
    # Only reuse evidence for the exact frozen revision of this case, never another case/site.
    reference = SavedScript.objects.filter(project=project, testcase=case, deleted=False,
        snapshot__revision=snapshot['revision'], source_result__status='pass',
        source_result__target_urls__contains=[urls[0]]).exclude(events=[]).first()
    events = []
    if reference:
        events = redact(reference.events, project)
        context['reference_script_id'] = reference.pk
        verified = reference.versions.filter(runs__status='pass').distinct().first()
        if verified and verified.parameters.get('base_url') == urls[0]:
            context['reference_version'] = {'code': verified.code, 'parameters': verified.parameters}
    script = SavedScript.objects.create(project=project, testcase=case, creator=user,
        name=case.name, snapshot=redact(snapshot, project), events=events,
        generation_mode='case', generation_context=redact(context, project),
        model_id=model.pk, generation_status='queued')
    if build_options:
        from .builds import start_build
        start_build(script, user, build_options)
    else:
        transaction.on_commit(lambda: enqueue_generation(script.pk, str(script.generation_token)))
    return script, True


def redact(value, project):
    """Keep artifacts free of configured credentials and common session secrets."""
    secrets = [str(c.password) for c in project.credentials.all() if c.password]
    def clean(item, key=''):
        if re.search(r'password|passwd|secret|token|authorization|cookie|storage.?state', key, re.I):
            return '[REDACTED]'
        if isinstance(item, dict):
            sensitive_input = any(re.search(r'password|passwd|密码|口令|token|secret', str(item.get(k, '')), re.I)
                                  for k in ('name', 'element', 'type', 'label'))
            return {k: '[REDACTED]' if sensitive_input and k in ('text', 'value') else clean(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item if not (isinstance(v, dict) and v.get('type') == 'image')]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, '[REDACTED]')
            item = re.sub(r'data:image/[^\s"\']+', '[IMAGE]', item)
            item = re.sub(r'(?i)((?:password|token|secret|cookie|authorization)[\s"\x27:=]+)[^\s,}\]"\x27]+', r'\1[REDACTED]', item)
            return item
        return item
    return clean(value)


def enable_capture(result):
    from langgraph_integration.models import LLMConfig
    config = LLMConfig.objects.filter(is_active=True).first()
    SavedScript.objects.get_or_create(source_result=result, defaults={
        'project': result.execution.project, 'testcase': result.testcase,
        'creator': result.execution.executor, 'name': result.case_snapshot.get('name', result.testcase.name),
        'snapshot': redact(result.case_snapshot, result.execution.project),
        'model_id': config.pk if config else None})
    result.execution.generate_playwright_script = True
    result.execution.save(update_fields=['generate_playwright_script'])


@transaction.atomic
def record_event(result_id, call_id, name, arguments, output):
    script = SavedScript.objects.select_for_update().filter(source_result_id=result_id, generation_status='recording').first()
    if not script:
        return
    if any(e.get('id') == call_id for e in script.events):
        return
    # Browser events plus screenshot uploads map the observed operations to case steps.
    if 'browser' not in name and 'screenshot' not in name:
        return
    output = redact(output, script.project)
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    event = redact({'id': call_id, 'tool': name, 'arguments': arguments, 'output': text[:24000]}, script.project)
    event['truncated'] = len(text) > 24000
    if len(script.events) >= 500:
        script.generation_error = '操作记录超过 500 条，无法完整保存；请缩小单条用例范围。'
    else:
        script.events = [*script.events, event]
    script.save(update_fields=['events', 'generation_error', 'updated_at'])


def finalize_capture(result_id):
    from .tasks import generate_script
    script = SavedScript.objects.filter(source_result_id=result_id, generation_status='recording').first()
    if not script:
        return
    changed = SavedScript.objects.filter(pk=script.pk, generation_status='recording').update(
        generation_status='queued', updated_at=timezone.now())
    if changed:
        try:
            generate_script.delay(script.pk, str(script.generation_token))
        except Exception:
            logger.exception('Failed to enqueue saved script %s', script.pk)
            SavedScript.objects.filter(pk=script.pk).update(generation_status='error', generation_error='脚本任务未能入队，请点击重新生成。')


@transaction.atomic
def save_version(script_id, code, parameters, origin='manual', base_version=None, token=None, extra_issues=None, warnings=None):
    from rest_framework.exceptions import ValidationError
    from .models import ScriptBuild
    from .acceptance import basic_build
    script = SavedScript.objects.select_for_update().get(pk=script_id)
    if token and str(script.generation_token) != str(token):
        return None
    latest = script.versions.first()
    if not isinstance(code, str) or len(code) > 100000:
        raise ValidationError('代码必须为文本，且不超过 100,000 字符。')
    if base_version is not None and base_version != (latest.number if latest else 0):
        raise ValidationError('脚本已有新版本，请重新载入后修改。')
    if not isinstance(parameters, dict) or any(not isinstance(k, str) or not isinstance(v, (str, int, float, bool)) for k, v in parameters.items()):
        raise ValidationError('参数必须是 JSON 对象，值支持文字、数字或布尔值。')
    if len(json.dumps(parameters)) > 20000:
        raise ValidationError('参数过长。')
    # Do not persist credential defaults. They can be provided from the runner environment.
    parameters = {k: v for k, v in parameters.items() if not re.search(r'password|secret|token|cookie', k, re.I)}
    code = redact(code, script.project)
    code, automatic_notes = normalize(code)
    build = (ScriptBuild.objects.select_for_update().filter(script=script, generation_token=token).first()
             if token and origin == 'guided' else None)
    basic = bool(build and basic_build(build))
    issues = list(dict.fromkeys([*validate(code, script.snapshot, parameters,
        require_assertions=not basic), *(extra_issues or [])]))
    if '[REDACTED]' in code:
        issues.append('代码中存在已脱敏数据，请改为参数引用。')
    notes = redact([*(warnings or []), *automatic_notes], script.project)
    # Guided builds have an explicit structured per-step review. Do not reinterpret
    # a non-blocking explanation with a keyword regex after that verdict.
    if origin == 'guided':
        warnings = notes
    else:
        issues, warnings = separate_notes(issues, notes, script.snapshot)
    version = ScriptVersion.objects.create(script=script, number=(latest.number + 1 if latest else 1),
        code=code, parameters=parameters, issues=issues, warnings=warnings, origin=origin)
    if basic:
        build.observation.setdefault('acceptance_versions', []).append(version.pk)
        build.save(update_fields=['observation', 'updated_at'])
        version._basic_acceptance = True
    script.generation_status = 'ready' if not issues else 'draft'
    script.generation_error = ''
    script.generation_token = uuid.uuid4()  # fences an older generation finishing after a manual save
    script.save(update_fields=['generation_status', 'generation_error', 'generation_token', 'updated_at'])
    return version
