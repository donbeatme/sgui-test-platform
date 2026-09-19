import json
from typing import Any


def _coerce_file_id(value: Any) -> int | None:
    if value in (None, ''):
        return None

    if isinstance(value, str):
        value = value.strip()
        if value.startswith('file_id:'):
            value = value.split(':', 1)[1]

    try:
        file_id = int(value)
    except (TypeError, ValueError):
        return None

    return file_id if file_id > 0 else None


def merge_file_ids(*groups: Any) -> list[int]:
    merged: list[int] = []
    seen: set[int] = set()

    def add(value: Any):
        file_id = _coerce_file_id(value)
        if file_id is not None and file_id not in seen:
            seen.add(file_id)
            merged.append(file_id)

    for group in groups:
        if group in (None, ''):
            continue
        if isinstance(group, str) and not group.strip().startswith('file_id:'):
            try:
                group = json.loads(group)
            except json.JSONDecodeError:
                pass
        if isinstance(group, (list, tuple, set)):
            for item in group:
                add(item)
        elif isinstance(group, dict):
            add(group.get('file_id'))
        else:
            add(group)

    return merged


def collect_file_ids_from_normalized_body(normalized_body: dict[str, Any] | None) -> list[int]:
    if not isinstance(normalized_body, dict):
        return []

    body_type = normalized_body.get('type')
    content = normalized_body.get('content')
    ids: list[int] = []

    if body_type == 'form-data' and isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or not item.get('enabled', True):
                continue
            value = item.get('value')
            file_id = None
            if item.get('value_type') == 'file':
                file_id = _coerce_file_id(item.get('file_id')) or _coerce_file_id(value)
            elif isinstance(value, str) and value.strip().startswith('file_id:'):
                file_id = _coerce_file_id(value)
            if file_id is not None:
                ids.append(file_id)
    elif body_type == 'binary':
        if isinstance(content, dict):
            file_id = _coerce_file_id(content.get('file_id')) or _coerce_file_id(content.get('id'))
            if file_id is not None:
                ids.append(file_id)
        else:
            file_id = _coerce_file_id(content)
            if file_id is not None:
                ids.append(file_id)

    return merge_file_ids(ids)


def collect_file_ids_from_body(body: Any) -> list[int]:
    try:
        from .payloads import normalize_request_body

        normalized_body = normalize_request_body(body)
    except Exception:
        return []

    return collect_file_ids_from_normalized_body(normalized_body)


def _strip_content_type_header(step_obj):
    headers = step_obj.struct().request.headers
    for key in list(headers.keys()):
        if str(key).lower() == 'content-type':
            headers.pop(key, None)


def apply_upload_files_to_step(step_obj, normalized_body, runtime_files):
    if not runtime_files or not isinstance(normalized_body, dict):
        return step_obj, False

    body_type = normalized_body.get('type')
    if body_type not in {'form-data', 'binary'}:
        return step_obj, False

    files_by_id = {item.get('id'): item for item in runtime_files if isinstance(item, dict)}
    upload_info = {}
    has_file = False

    if body_type == 'form-data':
        content = normalized_body.get('content')
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or not item.get('enabled', True):
                    continue

                key = item.get('key')
                if key in (None, ''):
                    continue

                key = str(key)
                value = item.get('value', '')
                file_id = None
                if item.get('value_type') == 'file':
                    file_id = _coerce_file_id(item.get('file_id')) or _coerce_file_id(value)
                elif isinstance(value, str) and value.strip().startswith('file_id:'):
                    file_id = _coerce_file_id(value)

                if file_id is not None:
                    file_info = files_by_id.get(file_id)
                    if file_info and file_info.get('path'):
                        upload_info[key] = str(file_info['path'])
                        has_file = True
                    continue

                upload_info[key] = '' if value is None else str(value)

    if body_type == 'binary':
        file_ids = collect_file_ids_from_normalized_body(normalized_body)
        file_id = file_ids[0] if file_ids else None
        file_info = files_by_id.get(file_id) if file_id is not None else None
        if not file_info and len(runtime_files) == 1:
            file_info = runtime_files[0]
        if file_info and file_info.get('path'):
            upload_info['file'] = str(file_info['path'])
            has_file = True

    if not has_file and len(runtime_files) == 1 and body_type in {'form-data', 'binary'}:
        file_info = runtime_files[0]
        if isinstance(file_info, dict) and file_info.get('path'):
            upload_info['file'] = str(file_info['path'])
            has_file = True

    if has_file:
        _strip_content_type_header(step_obj)
        step_obj = step_obj.upload(**upload_info)
        return step_obj, True

    return step_obj, False
