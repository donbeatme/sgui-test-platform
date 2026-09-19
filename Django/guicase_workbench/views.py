import copy
import json
import re
import tempfile
import uuid
from pathlib import Path
from PIL import Image
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from projects.models import Project, ProjectMember
from testcases.permissions import IsProjectMemberForTestCase
from langgraph_integration.models import LLMConfig
from requirements.models import RequirementDocument
from knowledge.models import KnowledgeBase
from .models import GenerationJob
from .limits import (MAX_TEXT_CHARS, MAX_DOCUMENTS, MAX_DOCUMENT_BYTES,
                     MAX_IMAGES, MAX_IMAGE_BYTES, MAX_UPLOAD_BYTES, upload_limits)
from .services import configuration, normalize_cases, inspect_cases, commit_job, canonical_cases, as_gui, DEFAULT_PROMPTS
from .tasks import generate_job, resolve_model
from .vendor.rules import DEFAULT_RULES, meta, _validate_rule


def job_data(job, full=True):
    data = {key: getattr(job, key) for key in ('id', 'title', 'status', 'stage', 'revision', 'saved_ids', 'created_at', 'updated_at')}
    data['count'] = len(job.cases)
    if full:
        state = job.progress or {}
        plan = state.get('plan', {})
        batches, outputs = state.get('batches', []), state.get('outputs', {})
        progress = {'phase': state.get('phase'), 'batch_total': len(batches), 'batch_done': len(outputs),
                    'sources': state.get('sources', []), 'coverage': state.get('coverage'),
                    'resumable': job.status in ['failed', 'cancelled'] and bool(job.options.get('prompts')),
                    'last_error': state.get('last_error'), 'modules': []}
        image_batches = state.get('vision_batches', [])
        image_outputs = state.get('vision_outputs', {})
        image_total = len(job.input.get('images', []))
        progress['vision'] = {
            'image_total': image_total,
            'image_done': image_total if state.get('vision_done') else sum(
                b['end'] - b['start'] for b in image_batches if b['id'] in image_outputs),
            'batch_total': len(image_batches),
            'batch_done': sum(b['id'] in image_outputs for b in image_batches),
        }
        for module in plan.get('modules', []):
            items = [b for b in batches if b['module_id'] == module['id']]
            progress['modules'].append({**module, 'batch_total': len(items),
                'batch_done': sum(b['id'] in outputs for b in items),
                'case_count': sum(len(outputs.get(b['id'], [])) for b in items)})
        data.update(cases=canonical_cases(job), issues=job.issues, notes=job.notes,
                    progress=progress,
                    input={k: v for k, v in job.input.items() if k != 'images'},
                    images=[{'name': a['name']} for a in job.input.get('images', [])])
    return data


class WorkbenchView(APIView):
    permission_classes = [IsAuthenticated, IsProjectMemberForTestCase]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.project = get_object_or_404(Project, pk=kwargs['project_pk'])
        perm = 'view' if request.method == 'GET' else 'add'
        if not request.user.has_perm(f'testcases.{perm}_testcase'):
            raise PermissionDenied('需要相应的用例管理权限。')

    def get(self, request, project_pk, operation=None, job_id=None):
        if job_id:
            job = get_object_or_404(GenerationJob, pk=job_id, project=self.project)
            if operation == 'export':
                return self.export(job, request.query_params.get('file_type', 'json'))
            return Response(job_data(job))
        config = configuration(self.project)
        return Response({
            'limits': upload_limits(),
            'settings': {'rules': config.rules, 'prompts': config.prompts, 'stages': config.stages},
            'rule_meta': meta(),
            'models': list(LLMConfig.objects.values('id', 'config_name', 'name', 'supports_vision', 'is_active')),
            'documents': list(RequirementDocument.objects.filter(project=self.project).values('id', 'title', 'word_count', 'image_count')[:200]),
            'knowledge_bases': list(KnowledgeBase.objects.filter(project=self.project).values('id', 'name')),
            'jobs': [job_data(j, full=False) for j in GenerationJob.objects.filter(project=self.project)[:100]],
            'can_configure': request.user.is_superuser or (request.user.has_perm('projects.change_project') and ProjectMember.objects.filter(project=self.project, user=request.user, role__in=['owner', 'admin']).exists()),
        })

    def post(self, request, project_pk, operation=None, job_id=None):
        if job_id:
            job = get_object_or_404(GenerationJob, pk=job_id, project=self.project)
            if operation == 'resume':
                if job.creator_id != request.user.pk and not request.user.is_superuser:
                    raise PermissionDenied('仅任务发起人可以继续任务。')
                with transaction.atomic():
                    Project.objects.select_for_update().get(pk=self.project.pk)
                    job = GenerationJob.objects.select_for_update().get(pk=job.pk)
                    if job.status not in ['failed', 'cancelled'] or not job.options.get('prompts'):
                        raise ValidationError('只有失败或取消的生成任务可以从断点继续。')
                    if GenerationJob.objects.filter(project=self.project, status__in=['queued', 'running']).count() >= 2:
                        raise ValidationError('当前项目已有 2 个进行中的任务，请等待或先取消。')
                    resolve_model(job.options, 'text', required=True)
                    job.run_token = uuid.uuid4()
                    job.status, job.stage = 'queued', '等待从断点继续'
                    job.notes = job.notes + ['用户继续任务：复用已完成进度；旧执行结果不会覆盖本轮。']
                    job.save()
                try:
                    generate_job.delay(str(job.pk), str(job.run_token))
                except Exception:
                    GenerationJob.objects.filter(pk=job.pk, run_token=job.run_token, status='queued').update(status='failed', stage='后台队列不可用，可再次继续', updated_at=timezone.now())
                    job.refresh_from_db()
                return Response(job_data(job))
            if operation == 'commit':
                return Response(job_data(commit_job(job.pk, self.project, request.user, request.data.get('revision'))))
            if operation == 'cancel':
                if job.creator_id != request.user.pk and not request.user.is_superuser:
                    raise PermissionDenied('仅任务发起人可以取消。')
                GenerationJob.objects.filter(pk=job.pk, status__in=['queued', 'running']).update(status='cancelled', stage='已取消')
                job.refresh_from_db()
                return Response(job_data(job))
            if operation == 'draft':
                with transaction.atomic():
                    job = GenerationJob.objects.select_for_update().get(pk=job.pk)
                    if job.status != 'draft' or job.revision != request.data.get('revision'):
                        raise ValidationError('草稿已更新或已保存，请刷新后再编辑。')
                    job.cases = normalize_cases(request.data.get('cases'), namespace=str(job.id))
                    job.issues = inspect_cases(job.cases, job.options['rules'], self.project)
                    job.revision += 1
                    job.save()
                return Response(job_data(job))
            raise ValidationError('不支持的任务操作。')
        config = configuration(self.project)
        if operation == 'settings':
            return self.save_settings(request, config)
        if operation in ['import', 'preset']:
            if operation == 'preset':
                payload = json.loads((Path(__file__).parent / 'presets/sgui_booking_ticketing.json').read_text(encoding='utf-8'))
                title = 'GuiCase 散客预订与出票 · 43 条预置用例'
            else:
                payload, title = request.data.get('payload'), str(request.data.get('title') or '导入用例')[:200]
            cases = normalize_cases(payload)
            report = inspect_cases(cases, config.rules, self.project)
            notes = ['源编号重复的条目会跳过，正式用例不会被覆盖。']
            if report['source_image_references']:
                notes.append('源数据中的截图名称仅为引用；本批 JSON 未附带图片文件。')
            if report['module_mappings']:
                notes.append('超出五级限制的模块，已将第五级及后续层级合并为末级名称（· 分隔）。原路径保存在 original_module_path，JSON 导出包含完整原值。')
            job = GenerationJob.objects.create(project=self.project, creator=request.user, title=title,
                status='draft', stage='导入预览待确认', cases=cases, issues=report,
                options={'rules': config.rules}, notes=notes)
            return Response(job_data(job), status=201)
        if operation == 'generate':
            if GenerationJob.objects.filter(project=self.project, status__in=['queued', 'running']).count() >= 2:
                raise ValidationError('当前项目已有 2 个进行中的任务，请等待完成或先取消。')
            stages = copy.deepcopy(config.stages)
            for key in ['vision', 'text']:
                raw = request.data.get(key + '_model_id')
                if raw:
                    try:
                        stages[key] = int(raw)
                    except (ValueError, TypeError):
                        raise ValidationError('模型 ID 无效。')
            options = {'rules': config.rules, 'prompts': {**DEFAULT_PROMPTS, **config.prompts}, 'stages': stages}
            resolve_model(options, 'text', required=True)
            data = self.prepare_input(request)
            if data['images']:
                resolve_model(options, 'vision', required=True)
            title = str(request.data.get('title') or '').strip()[:200]
            options['auto_title'] = not bool(title)
            job = GenerationJob.objects.create(project=self.project, creator=request.user,
                title=title or '待 AI 命名的用例任务', input=data, options=options)
            try:
                generate_job.delay(str(job.pk), str(job.run_token))
            except Exception:
                job.status, job.stage, job.notes = 'failed', '任务提交失败', ['后台队列不可用，请启动服务后重试。']
                job.save()
            return Response(job_data(job), status=201)
        raise ValidationError('不支持的操作。')

    def prepare_input(self, request):
        text = str(request.data.get('text', '')).strip()
        if len(text) > MAX_TEXT_CHARS:
            raise ValidationError(f'直接输入的需求最多 {MAX_TEXT_CHARS:,} 字符。')
        try:
            ids = json.loads(request.data.get('document_ids', '[]'))
            if not isinstance(ids, list) or len(ids) > MAX_DOCUMENTS:
                raise ValueError()
            ids = list(dict.fromkeys(str(uuid.UUID(str(i))) for i in ids))
        except (ValueError, TypeError):
            raise ValidationError(f'最多可以引用 {MAX_DOCUMENTS} 份有效的需求文档。')
        documents = RequirementDocument.objects.filter(project=self.project, pk__in=ids)
        if documents.count() != len(set(ids)):
            raise ValidationError('存在不属于当前项目的需求文档。')
        kb_id = request.data.get('knowledge_base_id') or None
        if kb_id and not KnowledgeBase.objects.filter(pk=kb_id, project=self.project).exists():
            raise ValidationError('知识库不属于当前项目。')
        image_files = request.FILES.getlist('images')
        docs = request.FILES.getlist('docs')
        if len(image_files) > MAX_IMAGES or len(docs) + len(ids) > MAX_DOCUMENTS:
            raise ValidationError(f'一次最多上传 {MAX_IMAGES} 张图片、关联 {MAX_DOCUMENTS} 份文档（含已有文档）。')
        if sum(f.size for f in [*docs, *image_files]) > MAX_UPLOAD_BYTES:
            raise ValidationError('本次上传文件合计不能超过 80 MB，请分批上传后引用已有文档。')
        image_info = []
        for f in image_files:
            if f.size > MAX_IMAGE_BYTES:
                raise ValidationError('单张图片不能超过 8 MB。')
            try:
                with Image.open(f) as img:
                    fmt = img.format
                    if fmt not in ['JPEG', 'PNG', 'WEBP'] or img.width * img.height > 25000000:
                        raise ValueError()
                    img.verify()
                f.seek(0)
            except Exception:
                raise ValidationError('图片必须是有效的 JPG、PNG 或 WebP，且不超过 2500 万像素。')
            image_info.append((f, {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}[fmt]))
        for f in docs:
            if f.size > MAX_DOCUMENT_BYTES or Path(f.name).suffix.lower() not in ['.txt', '.md', '.pdf', '.docx']:
                raise ValidationError('需求文档支持 TXT、MD、PDF、DOCX，单份最多 20 MB。')
            if not f.size:
                raise ValidationError(f'文档 {f.name} 是空文件，请重新选择。')
        # Validate vision before writing uploaded files.
        if image_files or request.data.get('include_document_images') == 'true':
            stages = configuration(self.project).stages.copy()
            if request.data.get('vision_model_id'):
                try:
                    stages['vision'] = int(request.data['vision_model_id'])
                except (ValueError, TypeError):
                    raise ValidationError('视觉模型 ID 无效。')
            resolve_model({'stages': stages}, 'vision', True)
        if not text and not docs and not ids and not image_files:
            raise ValidationError('请填写需求、选择文档或上传截图。')
        from requirements.services import DocumentProcessor
        for f in docs:
            doc = RequirementDocument.objects.create(project=self.project, uploader=request.user,
                title=Path(f.name).name[:200], document_type=Path(f.name).suffix[1:].lower(), file=f)
            doc.content = DocumentProcessor().extract_content(doc, force_file=True)
            doc.word_count, doc.status = len(doc.content or ''), 'uploaded'
            doc.save(update_fields=['content', 'word_count', 'status'])
            ids.append(str(doc.pk))
            if not (doc.content or '').strip() and not doc.images.exists():
                raise ValidationError(f'文档 {doc.title} 没有提取到内容。原文件已保留在需求管理，可检查后重试；扫描 PDF 需另行 OCR。')
        images = []
        if request.data.get('include_document_images') == 'true':
            from requirements.models import DocumentImage
            extracted = DocumentImage.objects.filter(document__project=self.project, document_id__in=ids)
            if extracted.count() + len(image_info) > MAX_IMAGES:
                raise ValidationError(f'包含文档内嵌图片后超过 {MAX_IMAGES} 张，请减少文档或关闭内嵌图片选项。')
            for img in extracted:
                if img.image_file.size > MAX_IMAGE_BYTES:
                    raise ValidationError('文档内嵌图片超过 8 MB，请先压缩。')
                images.append({'name': f'{img.document_id}/{img.image_id}', 'path': img.image_file.name, 'mime': img.content_type})
        for f, mime in image_info:
            path = default_storage.save(f'workbench/{self.project.pk}/{uuid.uuid4().hex}/{Path(f.name).name}', f)
            images.append({'name': Path(f.name).name, 'path': path, 'mime': mime})
        if not text and not images and not RequirementDocument.objects.filter(
                project=self.project, pk__in=ids).exclude(content__isnull=True).exclude(content='').exists():
            raise ValidationError('所选文档没有可用文字。扫描 PDF 需要先 OCR；仅含图片的 DOCX 请开启内嵌图片分析。')
        return {'text': text, 'document_ids': ids, 'knowledge_base_id': str(kb_id) if kb_id else None, 'images': images}

    def save_settings(self, request, config):
        if not request.user.is_superuser and not (request.user.has_perm('projects.change_project') and ProjectMember.objects.filter(project=self.project, user=request.user, role__in=['owner', 'admin']).exists()):
            raise PermissionDenied('仅有项目管理权限的项目管理员可以修改工作台配置。')
        data = request.data
        rules = data.get('rules', config.rules)
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValidationError('规则应为最多 100 条的数组。')
        try:
            seen = set()
            for rule in rules:
                _validate_rule(rule)
                if rule.get('id') in seen or not rule.get('id') or rule.get('level') not in ['error', 'warn']:
                    raise ValueError('规则 ID 必须唯一，级别为 error 或 warn。')
                seen.add(rule['id'])
                if rule.get('field') not in {f['key'] for f in meta()['fields']}:
                    raise ValueError('规则字段不在支持的字段列表中。')
                if rule['type'] in ['length', 'count']:
                    bounds = rule.get('value') or {}
                    lo, hi = bounds.get('min', 0), bounds.get('max', 1000000)
                    if not isinstance(lo, int) or not isinstance(hi, int) or not 0 <= lo <= hi <= 1000000:
                        raise ValueError('数量/长度范围必须为 0–1000000 的整数，且 min 不大于 max。')
                if rule['type'] in ['regex', 'not_regex']:
                    if len(str(rule['value'])) > 200:
                        raise ValueError('正则表达式最多 200 字符。')
                    re.compile(rule['value'])
                rule.get('message', '').format(value='', index=1, hit='')
            prompts = data.get('prompts', config.prompts)
            if not isinstance(prompts, dict) or set(prompts) - set(DEFAULT_PROMPTS) or any(not isinstance(v, str) or len(v) > 10000 for v in prompts.values()):
                raise ValueError('提示词必须使用支持的阶段名称，每项不超过 10000 字符。')
            stages = data.get('stages', config.stages)
            if not isinstance(stages, dict) or set(stages) - {'vision', 'text', 'validate', 'automation'}:
                raise ValueError('阶段名称无效。Embedding 统一使用项目知识库配置。')
            stages = {k: int(v) for k, v in stages.items() if v}
            if LLMConfig.objects.filter(pk__in=set(stages.values())).count() != len(set(stages.values())):
                raise ValueError('所选模型不存在。')
            if stages.get('vision'):
                resolve_model({'stages': stages}, 'vision', True)
        except (ValueError, TypeError, KeyError, AttributeError, re.error) as exc:
            raise ValidationError(str(exc))
        config.rules, config.prompts, config.stages = rules, prompts, stages
        config.save()
        return Response({'saved': True})

    def export(self, job, fmt):
        if job.status not in ['draft', 'saved']:
            raise ValidationError('任务尚无可导出的用例。')
        formats = {'json': 'application/json', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                   'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'feature': 'text/plain; charset=utf-8'}
        if fmt not in formats:
            raise ValidationError('支持 json、xlsx、docx、feature。')
        cases = canonical_cases(job)
        if fmt == 'json':
            content = json.dumps({'meta': {'title': job.title, 'source': 'SGUI自动化测试', 'job_id': str(job.pk)}, 'cases': cases}, ensure_ascii=False, indent=2).encode()
        else:
            from .vendor.template import export_excel, export_docx, export_feature
            folder = Path(settings.MEDIA_ROOT) / 'workbench_exports'
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=folder) as tmp:
                output = Path(tmp) / ('cases.' + fmt)
                {'xlsx': export_excel, 'docx': export_docx, 'feature': export_feature}[fmt]([as_gui(c) for c in cases], output)
                if fmt == 'xlsx':
                    from openpyxl import load_workbook
                    wb = load_workbook(output)
                    for ws in wb:
                        for row in ws:
                            for cell in row:
                                if cell.data_type == 'f':
                                    cell.data_type = 's'
                    wb.save(output)
                content = output.read_bytes()
        response = HttpResponse(content, content_type=formats[fmt])
        response['Content-Disposition'] = f'attachment; filename="cases-{str(job.pk)[:8]}.{fmt}"'
        return response
