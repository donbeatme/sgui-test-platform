from wharttest_django.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'guicase_workbench']
ROOT_URLCONF = 'workbench_urls'
STATIC_ROOT = '/app/static'
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# Recycle bloated children after a completed task; never terminate a running task.
CELERY_WORKER_MAX_TASKS_PER_CHILD = 20
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 512 * 1024  # KiB

# Product-facing title for the optional third-party skill registry.
SKILL_STORE_DEFAULT_SOURCE_NAME = '扩展技能库'
