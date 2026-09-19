import os
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management import call_command
from api_keys.models import APIKey
from mcp_tools.models import RemoteMCPConfig

# A fresh deployment has an empty database. Existing account passwords are kept.
User = get_user_model()
admin = User.objects.filter(username=os.environ['DJANGO_ADMIN_USERNAME']).first()
if admin is None:
    admin = User.objects.create_superuser(
        username=os.environ['DJANGO_ADMIN_USERNAME'],
        email=os.environ.get('DJANGO_ADMIN_EMAIL', ''),
        password=os.environ['DJANGO_ADMIN_PASSWORD'],
    )
    print('Created the configured SGUI administrator.')
APIKey.objects.update_or_create(name='Default MCP Key (Auto-generated)', defaults={
    'user': admin, 'key': os.environ['WHARTTEST_API_KEY'], 'is_active': True})
# Keep existing config IDs, keys and approval settings during the display-name upgrade.
for old_name, name, url in [
    ('Local WHartTest Tools', 'SGUI Platform Tools', 'http://mcp:8006/mcp/'),
    ('Local Playwright', 'SGUI Browser Tools', 'http://playwright-mcp:8931/mcp'),
]:
    config = RemoteMCPConfig.objects.filter(name=name).first()
    if config is None:
        config = RemoteMCPConfig.objects.filter(name=old_name, url=url).first()
    if config is None:
        RemoteMCPConfig.objects.create(name=name, url=url, transport='streamable-http', is_active=True)
    elif config.name == old_name:
        config.name = name
        config.save(update_fields=['name', 'updated_at'])
marker = Path('/app/data/.workbench_initialized')
if not marker.exists():
    # Cloned scheduled work must be explicitly re-enabled in this independent installation.
    from django_celery_beat.models import PeriodicTask
    PeriodicTask.objects.update(enabled=False)
    from task_center.models import ScheduledTask
    ScheduledTask.objects.update(status='disabled')
    from django.apps import apps
    for app, model in [('task_center', 'ScheduledTask'), ('api_testtasks', 'ApiTestTask')]:
        try:
            cls = apps.get_model(app, model)
            field_names = {f.name for f in cls._meta.fields}
            for field in ['enabled', 'is_enabled']:
                if field in field_names:
                    cls.objects.update(**{field: False})
        except LookupError:
            pass
    marker.write_text('Isolated installation initialized. Existing schedules disabled.\n')
call_command('collectstatic', interactive=False, verbosity=0)
print('SGUI workbench initialized; credentials remain server-side.')
