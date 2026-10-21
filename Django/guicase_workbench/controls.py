"""Reviewable visual controls. Image coordinates are evidence, never DOM locators."""
import copy
import json
import math
import uuid

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import grounding as ground

TYPES = ('button', 'input', 'select', 'checkbox', 'radio', 'link', 'tab', 'table', 'text', 'image', 'other')
SCHEMA = '''额外要求：每个 images 项必须增加 controls 数组（最多 40 个可见控件），每项为
{"name":"控件名称","type":"button|input|select|checkbox|radio|link|tab|table|text|image|other",
"text":"可见文案或占位符，无则空","state":"仅描述可见状态，不确定则写未知",
"bbox":[0.1,0.2,0.3,0.1]}。
bbox 顺序为左上角 x、y、宽、高，均相对整张原图归一化到 0–1，不能用像素坐标；不确定位置必须为 null。
x+宽与y+高均不得超过1；不要将右下角坐标误写成宽高。每张图片都须输出自己的 controls 数组。
同一控件只列一次，不把整页当成一个控件。facts 只保留控件清单未覆盖的状态和约束，不重复描述控件。
只提取画面证据，不从惯例推断隐藏规则。橙色/灰色不自动等于可点击/禁用；明确的勾选符号才支持选中状态。
先按本次业务范围筛选，再定位；排除明确不测的广告、全站导航，不逐个提取其控件。可操作选项按实际目标标注，不能给每个候选都复用整个列表的框。
无法可靠确定位置时 bbox=null；不为追求每个控件有框猜测坐标。一个控件名称只描述其画面作用，不加入点击后的行为。
控件清单与 facts 合计保持简明，unknowns 仅保留与本轮场景相关且材料未说明的实质问题。'''


def normalize_control(value):
    if not isinstance(value, dict):
        raise ValidationError('控件必须是对象。')
    clean = {}
    for key, limit in (('name', 120), ('text', 600), ('state', 300)):
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > limit:
            raise ValidationError(f'控件 {key} 必须为不超过 {limit} 字的文本。')
        clean[key] = text.strip()
    if not clean['name'] or value.get('type') not in TYPES:
        raise ValidationError('控件名称必填，类型须为支持的控件类型。')
    box = value.get('bbox')
    if box is not None:
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValidationError('标注位置须为 4 个有限数值，或留空。')
        x, y, w, h = box
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x+w <= 1.000001 and y+h <= 1.000001):
            raise ValidationError('标注超出原图范围；坐标为 0–1 的 x、y、宽、高。')
        box = [round(v, 6) for v in box]
    if type(value.get('excluded', False)) is not bool:
        raise ValidationError('排除标记必须为布尔值。')
    return {**clean, 'type': value['type'], 'bbox': box, 'excluded': value.get('excluded', False)}


def validate_vision(payload, names):
    result = ground.validate_vision(payload, names)
    for image in payload['images']:
        items = image.get('controls')
        if not isinstance(items, list) or len(items) > 40:
            raise ValidationError('图片分析必须返回 controls 数组，每图最多 40 个控件。')
        controls = []
        for item in items:
            # A guessed location must not discard usable text or invent a box.
            # Manual edits still use the strict normalize_control validator.
            if isinstance(item, dict) and item.get('bbox') is not None:
                clean = normalize_control({**item, 'bbox': None})
                try:
                    clean = normalize_control(item)
                except ValidationError:
                    clean['bbox_warning'] = '自动识别框无效，已留空；请对照原图核对或重新框选。'
                    clean['model_bbox'] = repr(item['bbox'])[:200]
                controls.append(clean)
            else:
                controls.append(normalize_control(item))
        for c in controls:
            c['excluded'] = False  # Only the user may exclude a detected control.
        result[image['name']]['facts'] = [f for f in result[image['name']]['facts'] if f['kind'] != 'control']
        result[image['name']]['controls'] = controls
        if len(json.dumps(result[image['name']], ensure_ascii=False)) > 12000:
            raise ValidationError('单图控件与分析合计超过 12000 字符，请精简描述。')
    return result


def material_text(unit):
    return json.dumps({'page': unit.get('page', unit['document']), 'facts': unit.get('visual_facts', []),
        'controls': [{k: v for k, v in c.items() if k != 'original'} for c in unit['controls'] if not c['excluded']],
        'excluded_controls': [{'name': c['name'], 'text': c['text']} for c in unit['controls'] if c['excluded']],
        'unknowns': unit.get('unknowns', [])}, ensure_ascii=False)


def sync_evidence(unit):
    ground.attach_evidence(unit, unit.get('visual_facts', []), unit.get('unknowns', []))
    for c in unit['controls']:
        if c['excluded']:
            continue
        unit['evidence_items'].append({'id': c['id'], 'unit_id': unit['id'], 'basis_type': 'ui_fact',
            'kind': 'control', 'document': unit['document'], 'pages': [], 'source_images': unit['source_images'],
            'text': f"{c['name']}（{c['type']}）；文案：{c['text'] or '未提供'}；可见状态：{c['state'] or '未知'}",
            'quote': c['text'], 'control_id': c['id'], 'bbox': c['bbox'], 'origin': c.get('origin', 'model')})
    unit['text'] = material_text(unit)


def initialize_unit(unit, page):
    unit['page'] = page['page']
    unit['visual_facts'] = copy.deepcopy(page['facts'])
    unit['unknowns'] = page['unknowns']
    unit['controls'] = [{**c, 'id': f"{unit['id']}-C{i:02d}", 'origin': 'model', 'original': copy.deepcopy(c)}
                        for i, c in enumerate(page['controls'], 1)]
    sync_evidence(unit)


def panel_data(job, cases):
    result = []
    for unit in job.progress.get('units', []):
        if unit.get('kind') != 'vision' or not unit.get('source_images'):
            continue
        items = unit.get('controls')
        legacy = items is None
        if legacy:
            items = [{'id': e['id'], 'name': e['text'], 'type': 'other', 'text': e.get('quote', ''),
                      'state': '', 'bbox': None, 'excluded': False, 'origin': 'legacy'}
                     for e in unit.get('evidence_items', []) if e.get('kind') == 'control']
        linked = []
        for c in items:
            refs = [case for case in cases if any(c['id'] in s.get('evidence_ids', []) for s in case['steps'])]
            linked.append({**c, 'case_ids': [x['tc_sno'] for x in refs], 'case_names': [x['tc_name'] for x in refs]})
        result.append({'unit_id': unit['id'], 'image_name': unit['source_images'][0], 'page': unit.get('page', unit['document']),
            'legacy': legacy, 'controls': linked, 'review': unit.get('control_review'),
            'edit_count': len(unit.get('control_history', []))})
    return result


def save_controls(job, payload, user, proceed=False):
    """Called with the job row locked. Reject stale writes and foreign control IDs."""
    if job.status != 'awaiting_controls' or job.revision != payload.get('revision'):
        raise ValidationError('控件核对阶段或版本已改变，请刷新后再操作。')
    by_id = {u['id']: u for u in job.progress['units'] if 'controls' in u}
    images = payload.get('images')
    if not isinstance(images, list) or len(images) != len(by_id) or any(not isinstance(i, dict) for i in images):
        raise ValidationError('请提交本任务全部图片的控件清单。')
    ids = [i.get('unit_id') for i in images]
    if any(not isinstance(i, str) for i in ids) or set(ids) != set(by_id) or len(set(ids)) != len(ids):
        raise ValidationError('图片编号缺失、重复或不属于本任务。')
    stamp = {'user_id': user.pk, 'username': user.get_username(), 'at': timezone.now().isoformat()}
    for image in images:
        unit = by_id[image['unit_id']]
        values = image.get('controls')
        if not isinstance(values, list) or len(values) > 60:
            raise ValidationError('人工核对后每张图最多保留 60 个控件。')
        old = {c['id']: c for c in unit['controls']}
        seen, new = set(), []
        for value in values:
            clean = normalize_control(value)
            cid = value.get('id')
            if cid:
                if not isinstance(cid, str) or cid not in old or cid in seen:
                    raise ValidationError('控件编号重复或不属于当前图片。')
                seen.add(cid)
                previous = old[cid]
                origin = previous.get('origin', 'model') if normalize_control(previous) == clean else 'human'
                new.append({**clean, 'id': cid, 'origin': origin, 'original': previous.get('original')})
            else:
                new.append({**clean, 'id': f"{unit['id']}-C{uuid.uuid4().hex[:12]}", 'origin': 'human', 'original': None})
        if seen != set(old):
            raise ValidationError('已有控件请标记为排除，不能直接删除其核对记录。')
        if type(image.get('reviewed')) is not bool:
            raise ValidationError('请明确标记本图片是否已核对。')
        changed = new != unit['controls']
        if changed:
            history = unit.setdefault('control_history', [])
            if len(history) >= 100:
                raise ValidationError('此图片核对修订已达 100 次，请新建任务继续。')
            history.append({**stamp, 'before': copy.deepcopy(unit['controls']), 'after': copy.deepcopy(new)})
        unit['controls'] = new
        unit['control_review'] = stamp if image['reviewed'] else None
        sync_evidence(unit)
        if len(unit['text']) > 16000:
            raise ValidationError('单张图片核对内容过长，请精简控件描述。')
    if proceed and any(not u.get('control_review') for u in by_id.values()):
        raise ValidationError('请逐张查看图片并标记“已核对本图”，再继续生成。')
    from .structure import make_groups
    job.progress['groups'] = make_groups(job.progress['units'])
    job.revision += 1
    if proceed:
        job.progress.update(controls_confirmed=True, phase='ground')
        job.status, job.stage, job.run_token = 'queued', '控件已核对，等待生成用例', uuid.uuid4()
        job.notes.append(f"{user.get_username()} 已核对 {len(by_id)} 张图片的控件，生成将使用核对后的清单；框选坐标只作图片证据。")
