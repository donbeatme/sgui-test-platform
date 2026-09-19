import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('guicase_workbench', '0001_initial')]
    operations = [
        migrations.AddField(model_name='generationjob', name='progress', field=models.JSONField(default=dict)),
        migrations.AddField(model_name='generationjob', name='run_token', field=models.UUIDField(default=uuid.uuid4, editable=False)),
    ]
