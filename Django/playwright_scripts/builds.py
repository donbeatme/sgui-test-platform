"""Live builds use the normal version, runner and evidence judge; no invented passes."""
import json
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import SavedScript, ScriptRun, ScriptWorker, ScriptBuild
from .services import redact, enqueue_generation, save_version
from .review import version_review
from .contract import validate
from .acceptance import REPLAY_ONCE, basic_build, required_replays
from testcases.models import TestExecution, TestCaseResult

ACTIVE_BUILDS = ('pending_observation', 'observing', 'queued', 'generating', 'validating')

def expire_builds():
    now = timezone.now()
    stale = (ScriptBuild.objects.filter(status='pending_observation', created_at__lt=now-timedelta(minutes=2)) |
             ScriptBuild.objects.filter(status='observing', heartbeat_at__lt=now-timedelta(seconds=60)) |
             ScriptBuild.objects.filter(status='queued', updated_at__lt=now-timedelta(hours=1)) |
             ScriptBuild.objects.filter(status='generating', updated_at__lt=now-timedelta(minutes=12)))
    for item in stale:
        if item.status == 'pending_observation' and item.created_at > now-timedelta(hours=1):
            if ScriptWorker.objects.filter(user=item.requester, worker_id=item.worker_id,
                    heartbeat_at__gt=now-timedelta(seconds=60)).exists():
                continue  # A live serial worker can be occupied by another build.
        with transaction.atomic():
            script = SavedScript.objects.select_for_update().get(pk=item.script_id)
            build = ScriptBuild.objects.select_for_update().get(pk=item.pk)
            if build.status != item.status or build.updated_at != item.updated_at:
                continue
            build.status, build.error = 'error', '实测生成服务离线或阶段超时，请重试。'
            build.save()
            script.generation_token = uuid.uuid4()
            script.generation_status, script.generation_error = 'error', build.error
            script.save()


def options_for(request_data, user):
    if request_data.get('validate_live', False) is not True:
        return None
    worker_id = request_data.get('worker_id', '')
    worker = ScriptWorker.objects.filter(worker_id=worker_id, user=user, runtime_version__gte=9,
        heartbeat_at__gt=timezone.now() - timedelta(seconds=30)).first()
    if not worker:
        raise ValidationError('实测生成需要在线的新版执行器，请启动或更新执行器后重试。')
    headless = request_data.get('headless', False)
    timeout = request_data.get('timeout_seconds', 180)
    if type(headless) is not bool or (not headless and not worker.supports_headed):
        raise ValidationError('该执行器不支持当前浏览器显示模式。')
    if type(timeout) is not int or not 30 <= timeout <= 600:
        raise ValidationError('每次实测超时须为 30–600 秒。')
    return {'worker_id': worker_id, 'headless': headless, 'timeout_seconds': timeout, 'strategy': 'guided-v2'}


@transaction.atomic
def start_build(script, user, options):
    script = SavedScript.objects.select_for_update().get(pk=script.pk)
    active = script.builds.filter(status__in=ACTIVE_BUILDS).first()
    if active:
        return active
    script.generation_token = uuid.uuid4()
    script.generation_status, script.generation_error = 'observing', ''
    script.save()
    settings = dict(options)
    strategy = settings.pop('strategy', 'legacy')
    observation = {'strategy': strategy}
    if strategy == 'guided-v2':
        observation['acceptance'] = REPLAY_ONCE
    return ScriptBuild.objects.create(script=script, requester=user,
        observation=observation, **settings)


def build_data(build):
    if not build:
        return None
    return {'id': build.pk, 'status': build.status, 'attempt': build.attempt,
            'max_attempts': build.max_repairs + 1, 'error': build.error,
            'acceptance': (build.observation or {}).get('acceptance', 'legacy'),
            'execution_id': build.last_run.result.execution_id if build.last_run_id else None,
            'diagnostic_only': bool(build.status == 'validating' and build.last_run_id and (build.last_run.result.model_info or {}).get('diagnostic_only')),
            'created_at': build.created_at, 'updated_at': build.updated_at}


def queue_generation(build):
    """Called with script and build locked, after recording observed evidence."""
    script = build.script
    cached = None
    if build.attempt == 0:
        # Avoid regenerating working code for the same frozen case. Relative
        # date defaults are only reused on their generation day; always rerun.
        for candidate in script.versions.filter(created_at__date=timezone.localdate(),
                runs__status='pass',runs__parameters__base_url=script.generation_context['base_url']).distinct():
            if candidate.origin in ('ai','ai_case') and not version_review(candidate)[0]:
                cached=candidate
                break
    script.generation_token = uuid.uuid4()
    script.generation_status, script.generation_error = 'queued', ''
    script.save()
    build.status, build.generation_token = 'queued', script.generation_token
    build.attempt += 1
    build.save()
    if cached:
        version=save_version(script.pk,cached.code,cached.parameters,origin=cached.origin,
            token=build.generation_token,warnings=[*cached.warnings,
                f'复用同一冻结用例当日已实测通过的 v{cached.number}，本轮仍重新验证；不沿用历史通过结论。'])
        generated(script.pk,build.generation_token,version)
        build.refresh_from_db()
        return
    transaction.on_commit(lambda: enqueue_generation(script.pk, str(script.generation_token)))


def generation_evidence(script, token):
    build = script.builds.filter(generation_token=token, status__in=['queued', 'generating']).first()
    if not build:
        return {}
    ScriptBuild.objects.filter(pk=build.pk, status='queued').update(status='generating', updated_at=timezone.now())
    return {'live_page': build.observation, 'build_id': build.pk,
            'attempt': build.attempt, 'max_attempts': build.max_repairs + 1}


@transaction.atomic
def generated(script_id, token, version, diagnostic_review=None):
    script = SavedScript.objects.select_for_update().get(pk=script_id)
    build = ScriptBuild.objects.select_for_update().filter(script=script, generation_token=token,
        status__in=['queued', 'generating']).first()
    if not build:
        return
    if version is None:
        build.status, build.error = 'error', script.generation_error or '生成未完成。'
        build.save()
        return
    issues, _ = version_review(version)
    from .guided import is_guided, restart_learning
    if issues and is_guided(build):
        restart_learning(build, {'review': issues, 'code': version.code})
        return
    if isinstance(diagnostic_review, dict):
        build.observation = {**(build.observation or {}), 'diagnostic_review': {
            'safe_actions': diagnostic_review.get('safe_actions') is True,
            'missing_evidence_only': diagnostic_review.get('missing_evidence_only') is True,
            'reason': str(diagnostic_review.get('reason') or '')[:1200],
        }}
    diagnostic = bool(issues and isinstance(diagnostic_review, dict)
        and diagnostic_review.get('safe_actions') is True
        and diagnostic_review.get('missing_evidence_only') is True
        and diagnostic_review.get('reason')
        and not validate(version.code, script.snapshot, version.parameters)
        and not ScriptRun.objects.filter(result__model_info__build_id=build.pk,
                                        result__model_info__diagnostic_only=True).exists())
    if issues and not diagnostic:
        build.status, build.error = 'draft', '代码或业务覆盖检查未通过；未自动执行。'
        build.save()
        return
    execution = TestExecution.objects.create(project=script.project, executor=build.requester,
        source='playwright_script', name=f'{script.name} · {"诊断采集（不计通过）" if diagnostic else "生成验证"} {build.attempt}',
        total_count=1, status='pending')
    result = TestCaseResult.objects.create(execution=execution, testcase=script.testcase,
        case_snapshot=script.snapshot, target_urls=[script.generation_context['base_url']],
        model_info={'judge': 'Playwright expect', 'script_id': script.pk, 'version': version.number,
                    'build_id': build.pk, 'build_attempt': build.attempt,
                    'acceptance': REPLAY_ONCE if basic_build(build) else 'legacy',
                    'diagnostic_only': diagnostic,
                    'action_review': diagnostic_review if diagnostic else None})
    build.last_run = ScriptRun.objects.create(version=version, result=result, parameters=version.parameters,
        worker_id=build.worker_id, worker_user=build.requester, headless=build.headless,
        timeout_seconds=build.timeout_seconds)
    build.status = script.generation_status = 'validating'
    script.save(update_fields=['generation_status', 'updated_at'])
    build.save()


def is_diagnostic_run(run):
    """A client cannot turn an arbitrary draft into a diagnostic job."""
    info=run.result.model_info or {}
    review=info.get('action_review') or {}
    return bool(info.get('diagnostic_only') is True and review.get('safe_actions') is True
        and review.get('missing_evidence_only') is True and review.get('reason')
        and ScriptBuild.objects.filter(pk=info.get('build_id'), script=run.version.script,
            last_run=run,status='validating').exists())


@transaction.atomic
def run_finished(run_id):
    # Lock script first, same order as manual save/cancellation.
    script_id = ScriptRun.objects.values_list('version__script_id', flat=True).get(pk=run_id)
    script = SavedScript.objects.select_for_update().get(pk=script_id)
    build = ScriptBuild.objects.select_for_update().filter(script=script, last_run_id=run_id,
        status='validating').select_related('last_run__result').first()
    if not build:
        return
    result = build.last_run.result
    from .guided import is_guided, restart_learning
    if is_guided(build):
        if result.status == 'pass':
            passed = ScriptRun.objects.filter(version=build.last_run.version,
                result__model_info__build_id=build.pk, result__status='pass').count()
            if passed < required_replays(build.last_run.version):
                build.status = 'generating'
                build.save(update_fields=['status', 'updated_at'])
                generated(script.pk, build.generation_token, build.last_run.version)
                return
            build.status, build.error = 'ready', ''
            script.generation_status = 'ready'
        elif result.status in ('fail', 'blocked'):
            restart_learning(build, {
                'error': result.error_message[:3000],
                'code': build.last_run.version.code,
                'page': (result.model_info or {}).get('replay_diagnostics', {})})
            return
        else:
            build.status = 'not_applicable' if result.status == 'skip' else result.status
            build.error = result.error_message or '生成验证未完成。'
            script.generation_status = 'error'
        build.save()
        script.save(update_fields=['generation_status', 'updated_at'])
        return
    if result.status in ('pass', 'skip'):
        build.status = 'ready' if result.status == 'pass' else 'not_applicable'
        build.error = result.error_message or ''
        script.generation_status = 'ready'
    elif result.status in ('fail', 'blocked') and build.attempt <= build.max_repairs:
        queue_generation(build)
        return
    else:
        build.status = result.status if result.status in ('blocked', 'cancelled', 'error') else 'failed'
        build.error = (result.error_message or '实测未通过，已达到自动修订次数。')[:4000]
        script.generation_status = 'ready'  # code still downloadable; actual validation stays explicit
    build.save()
    script.save(update_fields=['generation_status', 'updated_at'])


@transaction.atomic
def cancel_build(script, user):
    from testcases.execution_tracking import cancel_execution
    script = SavedScript.objects.select_for_update().get(pk=script.pk)
    build = ScriptBuild.objects.select_for_update().filter(script=script, status__in=ACTIVE_BUILDS).first()
    if not build:
        return
    build.status, build.error = 'cancelled', '用户停止实测生成。'
    build.save()
    script.generation_token = uuid.uuid4()
    script.generation_status = 'ready' if script.versions.exists() else 'error'
    script.generation_error = '' if script.versions.exists() else build.error
    script.save()
    if build.last_run_id:
        cancel_execution(build.last_run.result.execution_id)
        ScriptRun.objects.filter(pk=build.last_run_id, status__in=['pending', 'running']).update(status='cancelled')


def claim_observation(user, worker_id):
    with transaction.atomic():
        if ScriptBuild.objects.filter(requester=user, worker_id=worker_id, status='observing').exists():
            return None
        candidate = ScriptBuild.objects.filter(requester=user,
            worker_id=worker_id, status='pending_observation').order_by('id').first()
        if not candidate:
            return None
        SavedScript.objects.select_for_update().get(pk=candidate.script_id)
        build = ScriptBuild.objects.select_for_update(skip_locked=True).filter(
            pk=candidate.pk, status='pending_observation').first()
        if not build:
            return None
        from .guided import is_guided, claim
        if is_guided(build):
            return claim(build)
        build.status, build.heartbeat_at = 'observing', timezone.now()
        build.save()
        return {'kind': 'observe', 'id': build.pk, 'lease_token': str(build.lease_token),
                'base_url': build.script.generation_context['base_url'],
                'headless': build.headless, 'timeout_seconds': 90}


def observation_update(user, worker_id, operation, payload):
    from rest_framework.exceptions import PermissionDenied
    from django.shortcuts import get_object_or_404
    probe = get_object_or_404(ScriptBuild, pk=payload.get('run_id'), requester=user, worker_id=worker_id)
    if str(probe.lease_token) != str(payload.get('lease_token')):
        raise PermissionDenied('页面观察租约不匹配。')
    with transaction.atomic():
        script = SavedScript.objects.select_for_update().get(pk=probe.script_id)
        build = ScriptBuild.objects.select_for_update().get(pk=probe.pk)
        if operation == 'heartbeat':
            if build.status == 'observing':
                build.heartbeat_at = timezone.now()
                build.save(update_fields=['heartbeat_at', 'updated_at'])
            return {'cancelled': build.status != 'observing'}
        if operation != 'complete' or build.status != 'observing':
            return {'status': build.status}
        observation = payload.get('observation')
        if isinstance(observation, dict):
            encoded = json.dumps(observation, ensure_ascii=False)
            if len(encoded) > 100000:
                raise ValidationError('页面观察数据超出上限。')
            build.observation = redact(observation, script.project)
        if payload.get('status') != 'pass' or not isinstance(observation, dict):
            build.status, build.error = 'blocked', str(payload.get('summary') or '未能读取目标页面。')[:4000]
            script.generation_status, script.generation_error = 'error', build.error
            script.save()
        else:
            queue_generation(build)
        build.save()
        return {'status': build.status}
