"""Adapters keep WHartTest authoritative; GuiCase fields survive in an extension."""
import copy
import hashlib
import json
import re
from dataclasses import fields
from django.db import transaction
from rest_framework.exceptions import ValidationError
from projects.models import Project
from testcases.models import TestCase, TestCaseModule, TestCaseStep
from .models import CaseExtension, GenerationJob, WorkbenchSettings
from .vendor.schema import TestCase as GuiCase, Step
from .vendor.rules import DEFAULT_RULES, summary

DEFAULT_PROMPTS = {
    'vision': '分析界面截图中的控件、可见状态、交互和异常场景。区分可见事实与推测，不编造隐藏业务规则。',
    'vision_page': '逐张说明界面，并在描述中标注图片文件名。',
    'text': '基于提供的需求和界面分析生成中文测试用例，覆盖正常、异常、边界和权限。每一步填写可判定的预期结果。用例名称以“01 P1 场景”形式编写。',
    'validate': '检查用例的需求覆盖、步骤与预期的一致性和不可判定描述。仅返回简短中文问题清单，不改写用例。',
    'automation': '分析各用例哪些步骤适合 UI 自动化，列出需要用户补充的页面地址、定位器和测试数据。不要假定文字标签就是 CSS，也不要执行操作。',
}
LEVELS = {'高': 'P0', '中': 'P1', '低': 'P2'}


def configuration(project):
    obj, _ = WorkbenchSettings.objects.get_or_create(project=project, defaults={
        'rules': copy.deepcopy(DEFAULT_RULES), 'prompts': DEFAULT_PROMPTS.copy(), 'stages': {}})
    return obj


def source_key(case):
    # Stable across files/jobs: duplicate source IDs never silently overwrite work.
    return hashlib.sha256(str(case['tc_sno']).encode()).hexdigest()


def normalize_cases(payload, namespace='import'):
    if isinstance(payload, dict):
        payload = payload.get('cases')
    if not isinstance(payload, list) or not 1 <= len(payload) <= 200:
        raise ValidationError('请提供 1–200 条用例的 JSON 数组，或含 cases 数组的对象。')
    result, seen = [], set()
    for i, value in enumerate(payload, 1):
        if not isinstance(value, dict):
            raise ValidationError(f'第 {i} 条用例必须是对象。')
        c = copy.deepcopy(value)
        c['tc_name'] = c.get('tc_name', c.get('name', ''))
        c['module_path'] = c.get('module_path') or c.get('module_name') or '工作台导入'
        if not isinstance(c['module_path'], str):
            raise ValidationError(f'第 {i} 条模块路径必须是文本。')
        parts = [p.strip() for p in re.split(r'[/\\>]+', c['module_path']) if p.strip()]
        if len(parts) > 5:
            c.setdefault('original_module_path', c['module_path'])
            parts = parts[:4] + [' · '.join(parts[4:])]
        if not parts or any(len(p) > 100 for p in parts):
            raise ValidationError(f'第 {i} 条模块名称过长或为空；最多 5 层，每层不超过 100 字。')
        c['module_path'] = '/'.join(parts)
        c['module_name'] = parts[-1]
        if 'level' in c and not isinstance(c['level'], str):
            raise ValidationError(f'第 {i} 条优先级必须是文本。')
        c['priority'] = c.get('priority') or {'P0': '高', 'P1': '中', 'P2': '低', 'P3': '低'}.get(c.get('level'), '中')
        if not isinstance(c['priority'], str) or ('level' in c and not isinstance(c['level'], str)):
            raise ValidationError(f'第 {i} 条优先级必须是文本。')
        c['level'] = c.get('level') or LEVELS.get(c['priority'], '')
        for field, aliases in {'judge_type': {'正常': '正常场景', '异常': '异常场景'},
                               'data_type': {'典型': '典型值', '边界': '边界值', '枚举': '枚举值'},
                               'objective': {'新功能': '验证新功能', '回归': '回归测试'}}.items():
            if field in c:
                if not isinstance(c[field], str):
                    raise ValidationError(f'第 {i} 条 {field} 必须是文本。')
                c[field] = aliases.get(c[field], c[field])
        c['test_type'] = c.get('test_type') or ('exception' if c.get('judge_type') == '异常场景' else 'boundary' if c.get('data_type') == '边界值' else 'functional')
        steps = c.get('steps', [])
        if not isinstance(steps, list) or len(steps) > 100:
            raise ValidationError(f'第 {i} 条 steps 必须是最多 100 步的数组。')
        c['steps'] = []
        for n, s in enumerate(steps, 1):
            if not isinstance(s, dict):
                raise ValidationError(f'第 {i} 条第 {n} 步必须是对象。')
            c['steps'].append({**s, 'seq': n, 'desc': s.get('desc', s.get('description', '')),
                               'expected': s.get('expected', s.get('expected_result', ''))})
        if not c.get('tc_sno'):
            fingerprint = hashlib.sha256((namespace + json.dumps(c, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:18]
            c['tc_sno'] = 'GC-' + fingerprint
        c['tc_sno'] = str(c['tc_sno'])
        if len(c['tc_sno']) > 200 or c['tc_sno'] in seen:
            raise ValidationError(f'第 {i} 条源编号过长或在本批次中重复。')
        seen.add(c['tc_sno'])
        # Keep unknown properties in the JSON payload, while giving exports defaults.
        c = {**as_gui(c).to_dict(), **c}
        result.append(c)
    return result


def as_gui(c):
    body = {k: v for k, v in c.items() if k in GuiCase.__dataclass_fields__ and k != 'steps'}
    obj = GuiCase(**body)
    known = {f.name for f in fields(Step)}
    obj.steps = [Step(**{k: v for k, v in s.items() if k in known}) for s in c.get('steps', [])]
    return obj


def inspect_cases(cases, rules, project=None):
    for i, c in enumerate(cases, 1):
        if not isinstance(c.get('tc_name'), str) or not 1 <= len(c['tc_name'].strip()) <= 255:
            raise ValidationError(f'第 {i} 条名称必填，最多 255 字。')
        if c.get('level') not in dict(TestCase.LEVEL_CHOICES) or c.get('test_type') not in dict(TestCase.TEST_TYPE_CHOICES):
            raise ValidationError(f'第 {i} 条优先级或测试类型不符合平台定义。')
        for name in ('precondition', 'summary', 'remark', 'judge_type', 'data_type', 'objective', 'sub_objective', 'automation'):
            if not isinstance(c.get(name, ''), str):
                raise ValidationError(f'第 {i} 条 {name} 必须是文本。')
        for name in ('tags', 'source_images'):
            if not isinstance(c.get(name, []), list) or any(not isinstance(x, str) for x in c.get(name, [])):
                raise ValidationError(f'第 {i} 条 {name} 必须是文本数组。')
        for s in c['steps']:
            for name in ('desc', 'expected', 'action', 'target', 'value', 'locator'):
                if not isinstance(s.get(name, ''), str):
                    raise ValidationError(f'第 {i} 条步骤 {name} 必须是文本。')
    report = summary([as_gui(c) for c in cases], rules=rules)
    # Structural checks cannot be disabled by editing business rules.
    for i, c in enumerate(cases):
        errors = as_gui(c).validate()
        if any(not s['desc'].strip() for s in c['steps']):
            errors.append('每一步都需要操作描述。')
        if errors:
            report['errors'] += len(errors)
            report['detail'].append({'index': i, 'case': c['tc_name'], 'errors': len(errors), 'warnings': 0,
                                    'issues': [{'field': 'schema', 'level': 'error', 'message': e} for e in errors]})
    report['duplicates'] = []
    if project:
        keys = {source_key(c): c for c in cases}
        report['duplicates'] = list(CaseExtension.objects.filter(project=project, source_key__in=keys).values_list('case_id', flat=True))
    report['source_image_references'] = sorted({name for c in cases for name in c.get('source_images', [])})
    report['module_mappings'] = [{'source': c['original_module_path'], 'target': c['module_path']}
                                  for c in cases if c.get('original_module_path') and c['original_module_path'] != c['module_path']]
    return report


@transaction.atomic
def commit_job(job_id, project, user, revision):
    # Serialize writes per project, including root-module creation (NULL parent unique constraint).
    Project.objects.select_for_update().get(pk=project.pk)
    job = GenerationJob.objects.select_for_update().get(pk=job_id, project=project)
    if job.status == 'saved':
        return job
    if job.revision != revision or job.status != 'draft':
        raise ValidationError('草稿版本已改变或尚未生成完成，请刷新后重试。')
    cases = normalize_cases(job.cases, namespace=str(job.id))
    report = inspect_cases(cases, job.options['rules'], project)
    if report['errors']:
        raise ValidationError({'message': '存在规则错误，请修正草稿后保存。', 'issues': report})
    ids = []
    skipped = 0
    for c in cases:
        existing = CaseExtension.objects.filter(project=project, source_key=source_key(c)).first()
        if existing:
            ids.append(existing.case_id)
            skipped += 1
            continue
        parent = None
        for name in c['module_path'].split('/'):
            parent, _ = TestCaseModule.objects.get_or_create(project=project, parent=parent, name=name, defaults={'creator': user})
        case = TestCase.objects.create(project=project, module=parent, name=c['tc_name'],
                    precondition=c['precondition'], level=c['level'], test_type=c['test_type'],
                    creator=user, notes=c.get('remark', ''), review_status='pending_review')
        TestCaseStep.objects.bulk_create([TestCaseStep(test_case=case, step_number=n,
            description=s['desc'], expected_result=s['expected'], creator=user) for n, s in enumerate(c['steps'], 1)])
        CaseExtension.objects.create(case=case, project=project, source_key=source_key(c), original=c, job=job)
        ids.append(case.pk)
    job.status, job.stage, job.saved_ids = 'saved', '已保存至 SGUI 用例管理', ids
    job.notes = job.notes + [f'新增 {len(ids) - skipped} 条；按源编号跳过 {skipped} 条已存在的用例（未覆盖原内容）。']
    job.revision += 1
    job.save()
    return job


def canonical_cases(job):
    if job.status != 'saved':
        return job.cases
    result = []
    for case in TestCase.objects.filter(project=job.project, pk__in=job.saved_ids).select_related('module', 'guicase_extension').prefetch_related('steps'):
        original = copy.deepcopy(case.guicase_extension.original)
        parents, node = [], case.module
        while node:
            parents.insert(0, node.name)
            node = node.parent
        original.update(tc_name=case.name, precondition=case.precondition, level=case.level,
                        priority={'P0': '高', 'P1': '中', 'P2': '低', 'P3': '低'}[case.level],
                        test_type=case.test_type, remark=case.notes or '', module_path='/'.join(parents), module_name=parents[-1])
        old_steps = {s['seq']: s for s in original.get('steps', [])}
        original['steps'] = [{**old_steps.get(s.step_number, {}), 'seq': s.step_number, 'desc': s.description,
                              'expected': s.expected_result} for s in case.steps.all().order_by('step_number')]
        original['wharttest_id'] = case.pk
        result.append(original)
    return result
