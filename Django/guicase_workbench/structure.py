"""Source units preserve table records and headings before AI module planning."""
import hashlib
import json
import re
from collections import OrderedDict
from rest_framework.exceptions import ValidationError


def compact(text):
    return re.sub(r'\s+', '', text or '')


def text_units(text, document, kind='text'):
    # Prefer actual headings and paragraphs. PDF page boundaries are retained as
    # provenance, not treated as semantic boundaries when a heading is available.
    lines = text.splitlines(keepends=True)
    units, body, heading, page, start_page = [], [], '', None, None

    def flush():
        nonlocal body
        value = ''.join(body)
        if value.strip():
            units.append({'text': value, 'document': document, 'kind': kind,
                          'hint': heading, 'title': heading or document,
                          'pages': list(range(start_page, page + 1)) if start_page and page else []})
        body = []

    for line in lines:
        marker = re.match(r'=== 第(\d+)页 ===', line)
        title = re.match(r'^(#{1,6}\s+.+|第[一二三四五六七八九十\d]+[章节].+|\d+(?:\.\d+)+\s+\S.*)\s*$', line)
        if title:
            flush()
            heading = line.strip().lstrip('#').strip()
            start_page = page
        if marker:
            # Unstructured PDFs get page units with adjacent-page context later.
            if not heading:
                flush()
                start_page = int(marker[1])
            page = int(marker[1])
        body.append(line)
    flush()
    return units


def pdf_units(file, document):
    import pdfplumber
    units, current, headers = [], None, None
    statistics = {'pages': 0, 'tables': 0, 'records': 0, 'continuations': 0, 'header_overlap': 0}
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            statistics['pages'] += 1
            tables = page.find_tables()
            boxes = []
            for table in tables:
                rows = table.extract()
                if not rows:
                    continue
                boxes.append(table.bbox)
                statistics['tables'] += 1
                first = [compact(c) for c in rows[0]]
                # Some exported spreadsheets draw continued cell text in the
                # repeated header band. Infer header ink from a clean label cell,
                # then separate styles only inside that band (never across a page).
                if 'TcSno' in first and 'TcName' in first and 'ModuleName' in first:
                    from collections import Counter
                    cells = table.rows[0].cells
                    label_cell = cells[first.index('TcSno')]
                    colors = Counter(str(c.get('non_stroking_color')) for c in page.crop(label_cell).chars)
                    if colors:
                        ink = colors.most_common(1)[0][0]
                        recovered, residual = [], []
                        for cell in cells:
                            crop = page.crop(cell) if cell else None
                            recovered.append(compact(crop.filter(lambda o: str(o.get('non_stroking_color')) == ink).extract_text()) if crop else '')
                            residual.append((crop.filter(lambda o: str(o.get('non_stroking_color')) != ink).extract_text() or '') if crop else '')
                        if 'TcStep' in recovered and 'TcExpectedResult' in recovered:
                            first = recovered
                            if any(residual):
                                statistics['header_overlap'] += 1
                                # A path-only overflow belongs to the following
                                # new row. Step/expected overflow continues the
                                # preceding case and stays in order.
                                step_indexes = [first.index('TcStep'), first.index('TcExpectedResult')]
                                if len(rows) > 1 and not any(residual[i] for i in step_indexes) and compact(rows[1][first.index('TcSno')]):
                                    rows[1] = ['\n'.join(v for v in [residual[i], value] if v) for i, value in enumerate(rows[1])]
                                else:
                                    rows.insert(1, residual)
                if 'TcSno' in first and 'TcStep' in first and 'TcExpectedResult' in first:
                    headers = first
                    rows = rows[1:]
                elif not headers or len(first) != len(headers):
                    headers = None
                if headers:
                    for row in rows:
                        values = dict(zip(headers, [c or '' for c in row]))
                        sno = compact(values.get('TcSno'))
                        if sno == 'TcSno':
                            raise ValueError('Repeated header could not be separated reliably')
                        if sno or values.get('TcName', '').strip():
                            # Identical source IDs split over pages are continued.
                            if not current or not sno or sno != current.get('source_case_id'):
                                current = {'kind': 'case_table', 'document': document,
                                    'pages': [], 'hint': values.get('ModulePath', '').replace('\n', ''),
                                    'title': values.get('TcName', '').replace('\n', ''),
                                    'source_case_id': sno, 'fields': {k: v for k, v in values.items() if k not in ['TcStep', 'TcExpectedResult'] and v}, 'rows': []}
                                units.append(current)
                                statistics['records'] += 1
                        if current is None:
                            # Never attach an unidentified first row to a made-up case.
                            units.append({'kind': 'table', 'document': document, 'pages': [page.page_number],
                                          'hint': '', 'title': '未识别归属的表格行', 'text': json.dumps(values, ensure_ascii=False)})
                            continue
                        if current['pages'] and page.page_number not in current['pages']:
                            statistics['continuations'] += 1
                        if page.page_number not in current['pages']:
                            current['pages'].append(page.page_number)
                        if any(values.values()):
                            current['rows'].append({k: v for k, v in values.items() if v})
                else:
                    current = None
                    # Keep each row intact, repeating its column headings.
                    for row in rows[1:] or rows:
                        units.append({'kind': 'table', 'document': document, 'pages': [page.page_number],
                                      'hint': ' / '.join(first[:3])[:160], 'title': '表格行',
                                      'text': json.dumps({'columns': first, 'row': row}, ensure_ascii=False)})
            def outside(obj):
                x, y = obj.get('x0', 0), obj.get('top', 0)
                return not any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)
            remainder = page.filter(outside).extract_text() if boxes else page.extract_text()
            if remainder and remainder.strip():
                units.extend(text_units(f'=== 第{page.page_number}页 ===\n{remainder}', document, 'pdf_text'))
            page.close()  # Do not retain 64 pages of PDF layout objects in memory.
    for unit in units:
        if 'rows' in unit:
            unit['text'] = json.dumps({'source_case_id': unit['source_case_id'], 'fields': unit['fields'], 'steps_and_expected': unit['rows']}, ensure_ascii=False)
    return units, statistics


def bounded_units(units, limit=14000):
    """Size is a fallback only; split large records between complete step rows."""
    result = []
    for origin, unit in enumerate(units, 1):
        base = {k: v for k, v in unit.items() if k not in ['rows', 'fields', 'text']}
        base['origin'] = origin
        if len(unit['text']) <= limit:
            result.append({**base, 'text': unit['text']})
            continue
        if unit.get('rows'):
            prefix = json.dumps(unit['fields'], ensure_ascii=False) + '\n'
            pieces = [json.dumps(r, ensure_ascii=False) + '\n' for r in unit['rows']]
            if len(prefix) > limit // 3:
                prefix, pieces = '', [unit['text']]
        else:
            prefix = ''
            pieces = re.split(r'(?<=\n)\s*\n', unit['text'])
        groups, pending = [], ''
        for piece in pieces:
            # Huge single cells cannot fit whole: keep overlapping neighboring
            # text and the same origin ID so they stay in the same business group.
            while len(piece) + len(prefix) > limit:
                if pending:
                    groups.append(prefix + pending)
                    pending = ''
                room = limit - len(prefix)
                end = max(piece.rfind('\n', room // 2, room), piece.rfind('。', room // 2, room))
                end = end + 1 if end >= room // 2 else room
                groups.append(prefix + piece[:end])
                piece = piece[max(1, end - 800):]
            if pending and len(prefix + pending + piece) > limit:
                groups.append(prefix + pending)
                pending = ''
            pending += piece
        if pending:
            groups.append(prefix + pending)
        for part, value in enumerate(groups, 1):
            result.append({**base, 'text': value, 'part': f'{part}/{len(groups)}', 'continued': True})
    for index, unit in enumerate(result, 1):
        unit['id'] = f'U{index:04d}'
    return result


def collect_sources(job):
    from requirements.models import RequirementDocument
    from django.core.files.storage import default_storage
    from .limits import MAX_CONTEXT_CHARS
    units, notes, sources, raw_chars = [], [], [], len(job.input.get('text', ''))
    if job.input.get('text', '').strip():
        units.extend(text_units(job.input['text'], '补充需求说明'))
    for doc_id in job.input.get('document_ids', []):
        doc = RequirementDocument.objects.filter(pk=doc_id, project=job.project).first()
        if not doc:
            raise ValidationError('引用的文档已不存在，请调整材料后重试。')
        raw_chars += len(doc.content or '')
        if raw_chars > MAX_CONTEXT_CHARS:
            raise ValidationError(f'合并材料超过 {MAX_CONTEXT_CHARS:,} 字符上限，请减少文档范围。')
        content = doc.content or ''
        sources.append({'id': str(doc.pk), 'title': doc.title, 'chars': len(content), 'sha256': hashlib.sha256(content.encode()).hexdigest()})
        parsed = False
        if doc.document_type == 'pdf' and doc.file:
            # Respect edits to the authoritative requirement text. Reconstruct
            # tables from the PDF only if it still matches the saved text.
            from pypdf import PdfReader
            with default_storage.open(doc.file.name, 'rb') as stream:
                original = '\n\n'.join(f'=== 第{i+1}页 ===\n{t.strip()}' for i, p in enumerate(PdfReader(stream).pages) if (t := p.extract_text() or '').strip())
            if compact(original) == compact(content):
                try:
                    with default_storage.open(doc.file.name, 'rb') as stream:
                        extracted, stats = pdf_units(stream, doc.title)
                    if extracted:
                        units.extend(extracted)
                        notes.append(f'{doc.title}：按原 PDF 还原 {stats["pages"]} 页、{stats["tables"]} 张表格、{stats["records"]} 条用例记录，接续 {stats["continuations"]} 处跨页内容，分离 {stats["header_overlap"]} 处表头带重叠内容。')
                        parsed = True
                except Exception as exc:
                    notes.append(f'{doc.title}：表格还原未完成（{type(exc).__name__}），使用已保存文本及相邻上下文；请复核表格列与跨页关系。')
            else:
                notes.append(f'{doc.title}：已保存正文与原 PDF 不同，优先使用当前需求正文，保留页码和相邻上下文。')
        if not parsed:
            units.extend(text_units(content, doc.title, 'pdf_text' if doc.document_type == 'pdf' else 'text'))
    if raw_chars > MAX_CONTEXT_CHARS:
        raise ValidationError(f'合并材料超过 {MAX_CONTEXT_CHARS:,} 字符上限，请减少文档范围。')
    if not units and not job.input.get('images'):
        raise ValidationError('材料中没有可用文字，请检查文档解析结果。')
    repeated = {}
    for unit in units:
        if unit['kind'] == 'pdf_text':
            key = compact(re.sub(r'=== 第\d+页 ===', '', unit['text']))
            repeated[key] = repeated.get(key, 0) + 1
    for unit in units:
        if unit['kind'] == 'pdf_text':
            key = compact(re.sub(r'=== 第\d+页 ===', '', unit['text']))
            unit['context_only'] = len(key) < 500 and repeated.get(key, 0) >= 3
    units = bounded_units(units)
    notes.append(f'已冻结本次材料快照：{raw_chars:,} 字符，{len(units)} 个结构单元；续跑沿用同一快照。')
    if any(u.get('continued') for u in units):
        notes.append('少数结构单元过长，已在完整步骤/段落间细分；超长单格保留相邻重叠文字和同一来源编号，请复核接续关系。')
    return units, sources, notes


def make_groups(units):
    groups = OrderedDict()
    for unit in units:
        # Original module paths/headings are hints, not the final module decision.
        key = (unit['document'], unit.get('hint') or ('重复资料说明' if unit.get('context_only') else f'单元 {unit["id"]}'))
        group = groups.setdefault(key, {'id': f'G{len(groups)+1:03d}', 'document': key[0], 'hint': key[1], 'unit_ids': [], 'titles': [], 'preview': unit['text'][:240], 'context_only': True, 'needs_outline': False})
        group['needs_outline'] = group['needs_outline'] or (unit['kind'] != 'case_table' and not unit.get('context_only'))
        group['context_only'] = group['context_only'] and unit.get('context_only', False)
        group['unit_ids'].append(unit['id'])
        title = unit.get('title') or unit['text'][:80]
        if title not in group['titles']:
            group['titles'].append(title)
    return list(groups.values())


def validate_outline(payload, unit_ids):
    if not isinstance(payload, dict) or not isinstance(payload.get('units'), list):
        raise ValidationError('业务线索整理必须返回 units 数组。')
    result = {}
    for item in payload['units']:
        if not isinstance(item, dict) or item.get('id') not in unit_ids:
            raise ValidationError('业务线索必须引用本批已有单元编号。')
        titles, summary = item.get('topics'), item.get('summary')
        if not isinstance(titles, list) or not titles or any(not isinstance(t, str) or not t.strip() for t in titles) or not isinstance(summary, str) or not summary.strip():
            raise ValidationError('每个单元都需要业务主题数组和简要关系说明。')
        if len(json.dumps(item, ensure_ascii=False)) > 5000 or item['id'] in result:
            raise ValidationError('单元业务线索过长或编号重复。')
        result[item['id']] = {'topics': titles, 'summary': summary}
    if set(result) != set(unit_ids):
        raise ValidationError('业务线索整理遗漏了材料单元，请补齐所有编号。')
    return result


def catalog(groups):
    value = [{'id': g['id'], 'document': g['document'], 'original_module_or_heading': g['hint'],
              'titles': g['titles'], 'preview': g['preview'], 'context_only': g['context_only'], 'unit_count': len(g['unit_ids'])} for g in groups]
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > 60000:
        raise ValidationError('文档的模块/标题目录过大，请按业务范围减少文档后重试。原文没有被截断。')
    return text


def validate_plan(payload, groups):
    if not isinstance(payload, dict) or not isinstance(payload.get('modules'), list) or not 1 <= len(payload['modules']) <= 16:
        raise ValidationError('模块规划需要返回 1–16 个业务模块。')
    title = payload.get('title')
    if not isinstance(title, str) or not title.strip():
        raise ValidationError('模块规划未返回任务名称。')
    known = {g['id'] for g in groups}
    shared = payload.get('shared_group_ids', [])
    if not isinstance(shared, list) or any(not isinstance(g, str) or g not in known for g in shared):
        raise ValidationError('共享资料必须引用有效的资料分组。')
    used, paths, modules = set(shared), set(), []
    for i, value in enumerate(payload['modules'], 1):
        if not isinstance(value, dict):
            raise ValidationError('模块规划格式错误。')
        path = value.get('path')
        ids = value.get('group_ids')
        if not isinstance(path, str) or not isinstance(ids, list) or not ids or any(not isinstance(g, str) or g not in known for g in ids):
            raise ValidationError('模块规划必须引用本次材料中的有效资料分组。')
        parts = [p.strip() for p in path.split('/') if p.strip()]
        path = '/'.join(parts)
        if not 1 <= len(parts) <= 5 or any(len(p) > 100 for p in parts) or path in paths:
            raise ValidationError('模块路径必须唯一，最多五级，每级不超过 100 字。')
        modules.append({'id': f'M{i:02d}', 'name': parts[-1], 'path': path, 'group_ids': list(dict.fromkeys(ids)),
                        'description': str(value.get('description') or '')[:2000]})
        used.update(ids)
        paths.add(path)
    missing = known - used
    if missing:
        raise ValidationError('模块规划遗漏了资料分组：' + ', '.join(sorted(missing)) + '。请保留所有分组的业务归属。')
    return {'title': title.strip()[:200], 'modules': modules, 'shared_group_ids': list(dict.fromkeys(shared)),
            'shared_rules': str(payload.get('shared_rules') or '')[:5000]}


def make_batches(units, groups, plan, budget=16000):
    by_id = {u['id']: u for u in units}
    group_units = {g['id']: g['unit_ids'] for g in groups}
    batches = []
    for module in plan['modules']:
        ids = list(dict.fromkeys(u for g in module['group_ids'] for u in group_units[g]))
        pending, size = [], 0
        for uid in ids:
            amount = len(by_id[uid]['text'])
            if pending and size + amount > budget:
                batches.append({'module_id': module['id'], 'unit_ids': pending})
                pending, size = [], 0
            pending.append(uid)
            size += amount
        if pending:
            batches.append({'module_id': module['id'], 'unit_ids': pending})
    if len(batches) > 60:
        raise ValidationError('模块划分产生超过 60 批生成请求，请收窄范围或合并过细模块。')
    for i, batch in enumerate(batches, 1):
        batch['id'] = f'B{i:03d}'
    return batches


def batch_context(batch, units, plan):
    by_id = {u['id']: u for u in units}
    positions = {u['id']: i for i, u in enumerate(units)}
    module = next(m for m in plan['modules'] if m['id'] == batch['module_id'])
    materials = [{k: by_id[uid].get(k) for k in ['id', 'document', 'pages', 'hint', 'title', 'source_case_id', 'part', 'source_images', 'text']} for uid in batch['unit_ids']]
    neighbors = []
    # Supply neighboring text for prose or oversized units whose semantics may
    # continue across a boundary. Complete structured cases don't need slicing.
    for uid in [batch['unit_ids'][0], batch['unit_ids'][-1]]:
        unit = by_id[uid]
        if unit['kind'] == 'case_table' and not unit.get('continued'):
            continue
        index = positions[uid]
        for neighbor in [index - 1, index + 1]:
            if 0 <= neighbor < len(units) and units[neighbor]['id'] not in batch['unit_ids'] and units[neighbor]['document'] == unit['document']:
                n = units[neighbor]
                neighbors.append({'id': n['id'], 'context_only': True, 'text': n['text'][-1600:] if neighbor < index else n['text'][:1600]})
    return json.dumps({'task': plan['title'], 'module': module, 'module_outline': [{'path': m['path'], 'description': m['description']} for m in plan['modules']],
                       'shared_rules_to_verify_against_source': plan.get('shared_rules', ''),
                       'materials': materials, 'neighbor_context': neighbors}, ensure_ascii=False)
