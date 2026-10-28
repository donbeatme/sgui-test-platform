"""Authenticated client for the local screenshot perception service."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.exceptions import ValidationError


REGIONS = ('left_global_navigation', 'top_navigation_toolbar', 'tab_area',
           'main_workspace', 'bottom_action_bar')


def require_configuration():
    url = os.environ.get('GUI_PERCEPTION_URL', '').rstrip('/')
    token = os.environ.get('GUI_PERCEPTION_TOKEN', '')
    if not url or not token:
        raise ValidationError('截图识别服务未配置，请设置 GUI_PERCEPTION_URL 和 GUI_PERCEPTION_TOKEN。')
    return url, token


def recognize(asset):
    url, token = require_configuration()
    with default_storage.open(asset['path'], 'rb') as stream:
        content = stream.read()
    request = Request(url + '/recognize', content, method='POST', headers={
        'Content-Type': asset['mime'], 'Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, timeout=300) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise ValidationError(f'截图识别服务返回 HTTP {exc.code}，请检查感知服务状态。') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ConnectionError('无法连接截图识别服务，请检查服务地址与运行状态。') from exc
    except ValueError as exc:
        raise ValidationError('截图识别服务返回的数据不是有效 JSON。') from exc
    if not isinstance(result, dict) or not any(key in result for key in REGIONS):
        raise ValidationError('截图识别服务未返回五分区元素 JSON。')
    if isinstance(result.get('image'), dict):
        for key in ('path', 'source_path', 'source_image'):
            result['image'].pop(key, None)
        result['image']['source_filename'] = asset['name']
    return result


def store_result(job, index, result):
    """Keep the full output outside GenerationJob.progress, including icon crops."""
    import hashlib
    payload = json.dumps(result, ensure_ascii=False).encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()[:16]
    path = f'workbench/perception/{job.project_id}/{job.pk}/{index:03d}-{digest}.json'
    if not default_storage.exists(path):
        default_storage.save(path, ContentFile(payload))
    return path
