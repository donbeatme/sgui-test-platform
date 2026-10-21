"""Configuration readiness, without navigating or disclosing credential values."""
import copy
import re
from urllib.parse import urlsplit

from guicase_workbench.grounding import confirmation_reasons
from guicase_workbench.models import CaseExtension
from langgraph_integration.models import LLMConfig
from mcp_tools.models import RemoteMCPConfig, MCPTool


def urls_in(text):
    values = re.findall(r'https?://[^\s；，。<>"\'）)\]】]+', text or '')
    return list(dict.fromkeys(v.rstrip('.,;') for v in values))


def valid_url(value):
    try:
        if re.search(r'\s', value):
            return False
        parsed = urlsplit(value)
        parsed.port  # Reject malformed/out-of-range ports before any later parsing.
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def case_material(case, snapshot=None):
    extension = CaseExtension.objects.filter(case=case).first()
    value = copy.deepcopy(extension.original) if extension else {}
    steps = snapshot['steps'] if snapshot else list(case.steps.order_by('step_number').values('step_number', 'description', 'expected_result'))
    old = {s['seq']: s for s in value.get('steps', [])}
    value.update(tc_name=snapshot['name'] if snapshot else case.name,
                 precondition=(snapshot.get('precondition') if snapshot else case.precondition) or '')
    value['steps'] = [{**old.get(s['step_number'], {}), 'seq': s['step_number'],
                      'desc': s['description'], 'expected': s['expected_result']} for s in steps]
    return value, extension


def execution_preflight(case, snapshot=None):
    value, extension = case_material(case, snapshot)
    errors, warnings = [], []
    def error(code, message):
        errors.append({'code': code, 'message': message})
    def warn(code, message):
        warnings.append({'code': code, 'message': message})
    if case.review_status == 'unavailable':
        error('unavailable_case', '该用例已标记不可用，请先完成评审并恢复可用状态。')
    steps = value['steps']
    text = '\n'.join([value['precondition'], *[s['desc'] for s in steps]])
    explicit = urls_in(text)
    credentials = list(case.project.credentials.all())
    configured = list(dict.fromkeys(c.system_url.strip().rstrip('/') for c in credentials if c.system_url.strip()))
    targets = explicit or configured
    if not targets:
        error('missing_url', '请在项目管理的系统凭据中配置项目地址，或在本用例前置条件中指定网址。')
    elif not explicit and len(configured) > 1:
        error('ambiguous_url', '项目配置了多个不同地址，请在本用例前置条件中明确本次入口。')
    if any(not valid_url(u) for u in targets):
        error('invalid_url', '入口须为有效 HTTP/HTTPS 地址，且不能在网址中嵌入账号密码。')
    if not steps:
        error('missing_steps', '用例尚无可执行步骤。')
    for i, step in enumerate(steps, 1):
        if not (step['desc'] or '').strip() or not (step['expected'] or '').strip():
            error('missing_assertion', f'步骤 {i} 缺少操作或预期结果，请先补充。')
        elif step['expected'].strip() in ('正常', '成功', '操作成功', '符合预期', '正常显示'):
            warn('vague_assertion', f'步骤 {i} 的预期较笼统，建议补充可观察的文案、状态或数值。')
    if re.search(r'\{\{[^}]+\}\}|待提供|待补充|请补充|<账号>|<密码>|<网址>|<测试数据>', text + '\n' + '\n'.join(str(s.get('value', '')) for s in steps)):
        error('unresolved_data', '前置条件或步骤仍有未填的占位数据，请先配置或替换具体测试数据。')
    reasons = confirmation_reasons(value)
    if reasons:
        error('unconfirmed_basis', '用例依据仍需确认，请回到图文工作台打开来源任务，核对后单条或批量确认；补充说明选填。')
    if not value.get('grounding_version'):
        warn('legacy_basis', '该用例没有新版依据评审记录，请人工核对需求与预期。')
    if re.search(r'已登录|登录账号|正确账号|有效账号|登录系统|log.?in|signed.?in', text, re.I):
        matching = [c for c in credentials if not c.system_url or (valid_url(c.system_url) and any(urlsplit(c.system_url).netloc == urlsplit(u).netloc for u in targets if valid_url(u)))]
        if not any(c.username and c.password for c in matching):
            warn('login_data', '用例涉及登录，请确认测试账号、会话或登录步骤已准备；系统不能仅凭文字验证登录状态。')
    active = LLMConfig.objects.filter(is_active=True).first()
    if not active or not active.name or not valid_url(active.api_url):
        error('model', '请在模型配置中激活可用的模型并填写有效 API 地址。')
    if not RemoteMCPConfig.objects.filter(is_active=True).exists():
        error('tools', '没有启用的 MCP 服务，请先配置浏览器操作与截图上传工具。')
    else:
        names = list(MCPTool.objects.filter(mcp_config__is_active=True).values_list('name', flat=True))
        if not any('navigate' in n or 'browser' in n for n in names):
            warn('browser_tools', '未在已同步工具清单中确认浏览器工具，请在 MCP 管理中同步并检查。')
        if not any(('upload' in n and 'screenshot' in n) or n == 'save_operation_screenshots_to_the_application_case' for n in names):
            warn('evidence_tool', '未在已同步工具清单中确认截图上传工具；执行通过需要本轮步骤截图。')
    return {'ready': not errors, 'errors': errors, 'warnings': warnings,
            'target_urls': [u for u in targets if valid_url(u)], 'url_source': 'case' if explicit else 'project',
            'confirmation_reasons': reasons, 'source_job_id': str(extension.job_id) if extension and extension.job_id else None,
            'model': active.config_name if active else None,
            'scope': '仅检查配置与用例完整性，不代表网络、登录状态或业务断言已验证。'}


def prepare_execution(run):
    report = execution_preflight(run.testcase, run.case_snapshot)
    run.case_snapshot['preflight'] = report
    run.target_urls = report['target_urls']
    run.save(update_fields=['case_snapshot', 'target_urls'])
    return report
