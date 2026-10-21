import base64
import io
import json
import uuid
from datetime import timedelta
from PIL import Image
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from projects.models import Project
from testcases.permissions import IsProjectMemberForTestCase
from testcases.models import TestExecution, TestCaseResult, TestCaseScreenshot
from testcases.execution_tracking import finish_execution, cancel_execution
from testcases.preflight import valid_url
from .models import SavedScript, ScriptVersion, ScriptRun, ScriptWorker, ScriptBuild
from .services import save_version, redact, generate_from_case, enqueue_generation
from .contract import validate, parameter_keys, runtime_required
from .review import version_review
from .acceptance import REPLAY_ONCE, basic_version, required_replays, replay_verified
from .builds import (options_for, start_build, build_data, cancel_build, claim_observation,
                     observation_update, run_finished, expire_builds, ACTIVE_BUILDS, is_diagnostic_run)


def version_data(v):
    issues, warnings = version_review(v)
    return {'id': v.pk, 'number': v.number, 'code': v.code, 'parameters': v.parameters,
            'issues': issues, 'warnings': warnings, 'origin': v.origin, 'created_at': v.created_at,
            'acceptance': REPLAY_ONCE if basic_version(v) else 'legacy',
            'required_replays': required_replays(v), 'verified': replay_verified(v)}


def run_data(run):
    return {'id': run.pk, 'version': run.version.number, 'execution_id': run.result.execution_id,
            'status': run.result.status, 'error': run.result.error_message,
            'log': run.result.execution_log, 'step_results': run.result.step_results,
            'created_at': run.created_at, 'duration': run.result.duration}


def script_data(s, detail=False):
    latest = s.versions.first()
    data = {'id': s.pk, 'name': s.name, 'testcase_id': s.testcase_id,
            'generation_mode': s.generation_mode,
            'source_execution_id': s.source_result.execution_id if s.source_result_id else None,
            'status': s.generation_status, 'error': s.generation_error,
            'created_at': s.created_at, 'updated_at': s.updated_at,
            'latest': version_data(latest) if latest else None}
    data['build'] = build_data(s.builds.select_related('last_run__result').first())
    build = s.builds.first()
    from .guided import is_guided
    data['usable'] = bool(latest and not data['latest']['issues'] and data['latest']['verified'])
    if build and is_guided(build):
        data['usable'] = bool(data['usable'] and build.status == 'ready'
            and build.last_run_id and build.last_run.version_id == latest.pk)
        data['delivery_status'] = ('generating' if build.status in ACTIVE_BUILDS else
            'ready' if data['usable'] else 'cancelled' if build.status == 'cancelled' else 'failed')
    if latest and s.generation_status in ('ready', 'draft'):
        data['status'] = 'draft' if data['latest']['issues'] else 'ready'
    if detail:
        data['snapshot'] = s.snapshot
        data['versions'] = [version_data(v) for v in s.versions.all()]
        data['runs'] = [run_data(r) for r in ScriptRun.objects.filter(version__script=s).select_related('version', 'result').order_by('-id')[:50]]
    elif latest:
        data['latest'].pop('code', None)
    return data


def expire_runs():
    expire_builds()
    now = timezone.now()
    stale = ScriptRun.objects.filter(status__in=['pending', 'running'])
    for run in stale.select_related('result'):
        if run.result.status not in ('pending', 'running'):
            ScriptRun.objects.filter(pk=run.pk).update(status=run.result.status)
            run_finished(run.pk)
        elif (run.status == 'pending' and run.created_at < now - timedelta(minutes=2)) or (
                run.status == 'running' and run.heartbeat_at and run.heartbeat_at < now - timedelta(seconds=60)):
            if (run.status == 'pending' and run.created_at > now-timedelta(hours=1) and
                    ScriptWorker.objects.filter(worker_id=run.worker_id, user=run.worker_user,
                        heartbeat_at__gt=now-timedelta(seconds=60)).exists()):
                continue
            finish_execution(run.result_id, forced='error', reason='脚本执行器离线或未及时领取任务，请启动执行器后重试。')
            ScriptRun.objects.filter(pk=run.pk).update(status='error')
            run_finished(run.pk)


class ScriptView(APIView):
    permission_classes = [IsAuthenticated, IsProjectMemberForTestCase]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.project = get_object_or_404(Project, pk=kwargs['project_pk'])
        permission = 'view' if request.method == 'GET' else 'change'
        if not request.user.has_perm(f'testcases.{permission}_testcase'):
            raise PermissionDenied('需要相应的用例管理权限。')

    def script(self, pk):
        return get_object_or_404(SavedScript, pk=pk, project=self.project, deleted=False)

    def get(self, request, project_pk, script_id=None, operation=None):
        expire_runs()
        # A killed model worker must never leave the UI stuck at "generating".
        SavedScript.objects.filter(project=self.project, generation_status='generating',
            updated_at__lt=timezone.now()-timedelta(minutes=12)).update(
                generation_status='error', generation_error='脚本整理超时或服务已重启，请点击重新整理。', generation_token=uuid.uuid4())
        SavedScript.objects.filter(project=self.project, generation_status='queued',
            updated_at__lt=timezone.now()-timedelta(hours=1)).update(
                generation_status='error', generation_error='脚本排队超时，请检查任务服务后重试。', generation_token=uuid.uuid4())
        if not script_id:
            queryset = SavedScript.objects.filter(project=self.project, deleted=False).select_related('source_result').prefetch_related('versions')
            return Response({'scripts': [script_data(s) for s in queryset[:200]],
                'workers': list(ScriptWorker.objects.filter(user=request.user, heartbeat_at__gt=timezone.now() - timedelta(seconds=30)).values('worker_id', 'supports_headed', 'runtime_version'))})
        script = self.script(script_id)
        if operation == 'download':
            version = get_object_or_404(ScriptVersion, pk=request.query_params.get('version'), script=script)
            if version.origin == 'guided' and not replay_verified(version):
                raise ValidationError(f'脚本尚未完成所需的 {required_replays(version)} 次独立复跑，暂不能下载。')
            from .export import standalone
            return HttpResponse(standalone(version), content_type='text/x-python; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="case_{script.testcase_id}_v{version.number}.py"'})
        return Response(script_data(script, True))

    def post(self, request, project_pk, script_id=None, operation=None):
        if script_id is None:
            case_id = request.data.get('testcase_id')
            if type(case_id) is not int or case_id < 1:
                raise ValidationError('请选择有效的测试用例。')
            options = options_for(request.data, request.user)
            script, created = generate_from_case(self.project, request.user, case_id, request.data.get('base_url'), options)
            script.refresh_from_db()
            return Response(script_data(script, True), status=202 if created else 200)
        script = self.script(script_id)
        if operation == 'cancel-build':
            cancel_build(script, request.user)
            return Response({'message': '已停止实测生成，已有版本与执行记录保留。'})
        if operation == 'build':
            options = options_for({**request.data, 'validate_live': True}, request.user)
            if script.generation_mode != 'case':
                raise ValidationError('请从用例管理生成实测脚本。')
            if script.generation_status in ('queued', 'generating', 'recording'):
                raise ValidationError('请等待当前生成结束。')
            build = start_build(script, request.user, options)
            return Response(build_data(build), status=202)
        if operation in ('versions', 'regenerate') and script.builds.filter(status__in=ACTIVE_BUILDS).exists():
            raise ValidationError('请先停止实测生成，再修改或重新整理代码。')
        if operation == 'versions':
            # Editing code does not erase context such as fixed dates or shortened logs.
            previous = script.versions.first()
            version = save_version(script.pk, request.data.get('code', ''), request.data.get('parameters', {}),
                base_version=request.data.get('base_version', -1), warnings=previous.warnings if previous else [])
            return Response(version_data(version), status=201)
        if operation == 'regenerate':
            with transaction.atomic():
                script = SavedScript.objects.select_for_update().get(pk=script.pk)
                if script.generation_status in ('recording', 'queued', 'generating'):
                    raise ValidationError('脚本正在记录或生成，请稍后再试。')
                if script.generation_mode != 'case' and not script.events:
                    raise ValidationError('没有本次操作记录，请重新执行并勾选保存。')
                script.generation_token = uuid.uuid4()
                script.generation_status, script.generation_error = 'queued', ''
                script.save()
                transaction.on_commit(lambda: enqueue_generation(script.pk, str(script.generation_token)))
            return Response({'message': '已重新整理脚本，将保存为新版本。'})
        if operation == 'run':
            expire_runs()
            with transaction.atomic():
                script = SavedScript.objects.select_for_update().get(pk=script.pk)
                if script.builds.filter(status__in=ACTIVE_BUILDS).exists():
                    raise ValidationError('实测生成正在进行，请完成或停止后再单独运行。')
                version = get_object_or_404(ScriptVersion, pk=request.data.get('version'), script=script)
                if version.origin == 'guided' and not replay_verified(version):
                    raise ValidationError('生成验证尚未完成，请等待脚本显示可用后再运行。')
                issues, _ = version_review(version)
                if issues:
                    raise ValidationError({'issues': issues})
                if not script.testcase_id:
                    raise ValidationError('来源用例已删除，不能创建新的执行。')
                if ScriptRun.objects.filter(version__script=script, status__in=['pending', 'running']).exists():
                    raise ValidationError('该脚本已有执行中的任务，请等待完成或先停止。')
                worker_id = request.data.get('worker_id', '')
                worker = ScriptWorker.objects.filter(worker_id=worker_id, user=request.user, heartbeat_at__gt=timezone.now()-timedelta(seconds=30)).first()
                if not worker:
                    raise ValidationError('执行器未在线，请启动执行器后刷新列表。')
                if worker.runtime_version < max(runtime_required(version.code, version.parameters), 9 if basic_version(version) else 0):
                    raise ValidationError('该脚本使用新版运行能力，请更新并重启执行器后运行。')
                headless = request.data.get('headless', False)
                if type(headless) is not bool or (not headless and not worker.supports_headed):
                    raise ValidationError('该执行器不支持可见浏览器，请选择无头模式。')
                supplied = request.data.get('parameters', {})
                if not isinstance(supplied, dict) or len(json.dumps(supplied)) > 20000 or any(not isinstance(v, (str, int, float, bool)) for v in supplied.values()):
                    raise ValidationError('运行参数必须是简单 JSON 对象。')
                parameters = {**version.parameters, **supplied}
                # A configured project URL supersedes the captured site by default.
                urls = list(dict.fromkeys(c.system_url.strip() for c in self.project.credentials.all() if c.system_url.strip()))
                inherit_project = script.generation_mode != 'case' or script.generation_context.get('url_source') == 'project'
                if inherit_project and 'base_url' not in supplied and len(urls) == 1:
                    parameters['base_url'] = urls[0]
                if inherit_project and 'base_url' not in supplied and len(urls) > 1:
                    raise ValidationError('项目有多个地址，请指定本次 base_url。')
                if not valid_url(str(parameters.get('base_url', ''))):
                    raise ValidationError('请在项目中配置地址，或在运行参数中指定有效 base_url。')
                cleaned = redact(parameters, self.project)
                if cleaned != parameters:
                    raise ValidationError('敏感参数请通过执行器环境变量 SGUI_PARAM_参数名 提供，不写入运行记录。')
                parameter_issues = validate(version.code, script.snapshot, parameters,
                    require_assertions=not basic_version(version))
                if parameter_issues:
                    raise ValidationError({'issues': parameter_issues})
                timeout = request.data.get('timeout_seconds', 600)
                if type(timeout) is not int or not 30 <= timeout <= 1800:
                    raise ValidationError('总超时须为 30–1800 秒。')
                execution = TestExecution.objects.create(project=self.project, executor=request.user, source='playwright_script',
                    name=f'{script.name} · 脚本 v{version.number}', total_count=1, status='pending')
                result = TestCaseResult.objects.create(execution=execution, testcase=script.testcase, case_snapshot=script.snapshot,
                    target_urls=[parameters['base_url']], model_info={'judge': 'Playwright expect', 'script_id': script.pk,
                        'version': version.number, 'acceptance': REPLAY_ONCE if basic_version(version) else 'legacy'})
                run = ScriptRun.objects.create(version=version, result=result, parameters=parameters,
                    worker_id=worker.worker_id, worker_user=worker.user, headless=headless, timeout_seconds=timeout)
            return Response(run_data(run), status=201)
        if operation == 'stop':
            run = get_object_or_404(ScriptRun, pk=request.data.get('run_id'), version__script=script)
            cancel_execution(run.result.execution_id)
            ScriptRun.objects.filter(pk=run.pk).update(status='cancelled')
            run_finished(run.pk)
            return Response({'message': '已请求停止，执行器将在数秒内关闭本次浏览器。'})
        raise ValidationError('未知操作。')

    def delete(self, request, project_pk, script_id):
        with transaction.atomic():
            script = SavedScript.objects.select_for_update().get(pk=self.script(script_id).pk)
            if script.builds.filter(status__in=ACTIVE_BUILDS).exists():
                raise ValidationError('请先停止实测生成。')
            if ScriptRun.objects.filter(version__script=script, status__in=['pending', 'running']).exists():
                raise ValidationError('请先停止正在运行的脚本。')
            script.deleted = True
            script.generation_token = uuid.uuid4()
            script.save()
        return Response({'message': '已移除脚本，历史执行记录及其版本保留。'})


class WorkerView(APIView):
    """Authenticated trusted actuator: leased jobs, fenced completions, no anonymous callbacks."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, operation):
        expire_runs()
        worker_id = str(request.data.get('worker_id', ''))[:120]
        if not worker_id:
            raise ValidationError('缺少执行器编号。')
        if request.data.get('kind') == 'guided':
            from .guided import update
            ScriptWorker.objects.filter(worker_id=worker_id, user=request.user).update(heartbeat_at=timezone.now())
            return Response(update(request.user, worker_id, operation, request.data))
        if request.data.get('kind') == 'observe':
            ScriptWorker.objects.filter(worker_id=worker_id, user=request.user).update(heartbeat_at=timezone.now())
            return Response(observation_update(request.user, worker_id, operation, request.data))
        if operation == 'claim':
            runtime = request.data.get('runtime_version', 0)
            if type(runtime) is not int or not 0 <= runtime <= 100:
                raise ValidationError('执行器版本无效。')
            ScriptWorker.objects.update_or_create(worker_id=worker_id, user=request.user, defaults={
                'heartbeat_at': timezone.now(), 'supports_headed': request.data.get('supports_headed') is True,
                'runtime_version': runtime})
            with transaction.atomic():
                ScriptWorker.objects.select_for_update().get(worker_id=worker_id, user=request.user)
                if (ScriptRun.objects.filter(worker_id=worker_id, worker_user=request.user, status='running').exists()
                        or ScriptBuild.objects.filter(worker_id=worker_id, requester=request.user, status='observing').exists()):
                    return Response({'job': None})
                run = ScriptRun.objects.select_for_update(skip_locked=True).filter(status='pending', worker_id=worker_id, worker_user=request.user).order_by('id').first()
                if not run:
                    return Response({'job': claim_observation(request.user, worker_id) if runtime >= 4 else None})
                if run.result.status not in ('pending', 'running'):
                    run.status = run.result.status
                    run.save(update_fields=['status'])
                    return Response({'job': None})
                errors, _ = version_review(run.version)
                diagnostic = is_diagnostic_run(run)
                if diagnostic:
                    errors = []  # Coverage is deliberately unresolved, never a passing test.
                basic = basic_version(run.version)
                errors += validate(run.version.code, run.version.script.snapshot, run.parameters, require_assertions=not basic)
                if runtime < max(runtime_required(run.version.code, run.parameters), 9 if basic else 0,
                                 6 if diagnostic else 4 if (run.result.model_info or {}).get('build_id') else 0):
                    finish_execution(run.result_id, forced='error', reason='执行器版本不支持此脚本的运行能力，请更新执行器。')
                    run.status = 'error'
                    run.save(update_fields=['status'])
                    transaction.on_commit(lambda: run_finished(run.pk))
                    return Response({'job': None})
                if errors:
                    finish_execution(run.result_id, forced='error', reason='脚本格式校验未通过。')
                    run.status = 'error'
                    run.save(update_fields=['status'])
                    transaction.on_commit(lambda: run_finished(run.pk))
                    return Response({'job': None})
                now = timezone.now()
                run.status, run.heartbeat_at = 'running', now
                run.save(update_fields=['status', 'heartbeat_at'])
                TestExecution.objects.filter(pk=run.result.execution_id).update(status='running', started_at=now)
                TestCaseResult.objects.filter(pk=run.result_id).update(status='running', started_at=now)
                return Response({'job': {'id': run.pk, 'lease_token': str(run.lease_token), 'code': run.version.code,
                    'parameters': run.parameters, 'snapshot': run.version.script.snapshot, 'headless': run.headless,
                    'capture_evidence': bool((run.result.model_info or {}).get('build_id')),
                    'diagnostic_only': diagnostic,
                    'acceptance': REPLAY_ONCE if basic else 'legacy',
                    'timeout_seconds': run.timeout_seconds}})
        run = get_object_or_404(ScriptRun, pk=request.data.get('run_id'), worker_id=worker_id, worker_user=request.user)
        if str(run.lease_token) != str(request.data.get('lease_token')):
            raise PermissionDenied('执行租约不匹配。')
        ScriptWorker.objects.filter(worker_id=worker_id, user=request.user).update(heartbeat_at=timezone.now())
        if operation == 'heartbeat':
            if run.status == 'running':
                ScriptRun.objects.filter(pk=run.pk).update(heartbeat_at=timezone.now())
            return Response({'cancelled': run.status != 'running' or run.result.status != 'running'})
        if operation == 'complete':
            with transaction.atomic():
                # Parent-first locking agrees with cancellation and finish_execution.
                TestExecution.objects.select_for_update().get(pk=run.result.execution_id)
                run = ScriptRun.objects.select_for_update().get(pk=run.pk)
                run.result.refresh_from_db()
                if run.status != 'running' or run.result.status != 'running':
                    return Response({'status': run.result.status})
                items = request.data.get('steps', [])
                originals = {s['step_number']: s for s in run.result.case_snapshot.get('steps', [])}
                if not isinstance(items, list) or len(items) > len(originals):
                    raise ValidationError('步骤结果无效。')
                steps = []
                seen = set()
                for item in items:
                    if not isinstance(item, dict):
                        raise ValidationError('步骤结果格式无效。')
                    number = item.get('step_number')
                    if type(number) is not int or number not in originals or number in seen:
                        raise ValidationError('步骤编号无效。')
                    seen.add(number)
                    ids = []
                    final_picture = item.get('screenshot', '')
                    recovery_pictures = item.get('recovery_screenshots', [])
                    if not isinstance(recovery_pictures, list) or len(recovery_pictures) > 4:
                        raise ValidationError('恢复现场截图格式无效，单步最多保留 4 张。')
                    pictures = ([(final_picture, '')] if final_picture else []) + [
                        (picture, f' · 恢复前失败现场 {index}') for index, picture in enumerate(recovery_pictures, 1)]
                    for picture, suffix in pictures:
                        try:
                            if not isinstance(picture, str):
                                raise ValueError('invalid image type')
                            if len(picture) > 12 * 1024 * 1024:
                                raise ValueError('image too large')
                            image_bytes = base64.b64decode(picture, validate=True)
                            with Image.open(io.BytesIO(image_bytes)) as image:
                                if image.format != 'PNG' or image.width * image.height > 20000000:
                                    raise ValueError('invalid image')
                                image.verify()
                        except Exception as exc:
                            raise ValidationError('截图无效。') from exc
                        shot = TestCaseScreenshot(test_case=run.result.testcase, execution_result=run.result,
                            step_number=number, title=f'Playwright v{run.version.number} · 步骤 {number}{suffix}', uploader=request.user)
                        shot.screenshot.save(f'script-run-{run.pk}-step-{number}-{len(ids)}.png', ContentFile(image_bytes), save=True)
                        ids.append(shot.pk)
                    step_status = item.get('status')
                    actual = str(item.get('actual', ''))
                    if step_status in ('pass', 'skip') and not final_picture and not basic_version(run.version):
                        step_status = 'blocked'
                        actual += '；缺少步骤结束截图，恢复前截图不能作为通过依据。'
                    steps.append({'step_number': number, 'expected': originals[number]['expected_result'],
                        'status': step_status, 'actual': redact(actual[:5000], run.version.script.project), 'screenshot_ids': ids})
                    condition = item.get('condition')
                    if isinstance(condition, dict):
                        matched = condition.get('matched')
                        if isinstance(matched, list) and len(matched) <= 8 and all(isinstance(s, str) for s in matched):
                            steps[-1]['condition'] = redact({
                                'matched': [s[:200] for s in matched],
                                'outcome': str(condition.get('outcome', ''))[:20],
                                'reason': str(condition.get('reason', ''))[:1000],
                            }, run.version.script.project)
                outcome = request.data.get('status', 'error')
                diagnostic = is_diagnostic_run(run)
                if diagnostic and outcome not in ('cancelled', 'error'):
                    outcome = 'blocked'
                forced = outcome if outcome in ('cancelled', 'error', 'blocked') else None
                log = redact(str(request.data.get('log', ''))[:60000], run.version.script.project)
                diagnostics = request.data.get('diagnostics', {})
                if isinstance(diagnostics, dict):
                    allowed = {}
                    for key, limit in [('url', 2000), ('title', 1000), ('aria_snapshot', 24000), ('dom', 40000), ('note', 1000), ('step_observations', 36000)]:
                        if isinstance(diagnostics.get(key), str):
                            allowed[key] = diagnostics[key][:limit]
                    if type(diagnostics.get('step_number')) is int and diagnostics['step_number'] in originals:
                        allowed['step_number'] = diagnostics['step_number']
                    run.result.model_info = {**(run.result.model_info or {}),
                        'replay_diagnostics': redact(allowed, run.version.script.project)}
                    run.result.save(update_fields=['model_info'])
                report = {'status': outcome, 'summary': redact(str(request.data.get('summary', ''))[:4000], run.version.script.project), 'steps': steps}
                if diagnostic:
                    report['summary'] = '诊断采集，不计为业务通过；需补齐原始断言并重新实测。' + report['summary']
                result = finish_execution(run.result_id, report, forced=forced, reason=report['summary'], log=log)
                run.status = result.status
                run.save(update_fields=['status'])
                transaction.on_commit(lambda: run_finished(run.pk))
            return Response({'status': result.status})
        raise ValidationError('未知执行器操作。')
