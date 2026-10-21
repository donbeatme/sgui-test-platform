from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('playwright_scripts', '0003_scriptworker_runtime_version')]
    operations = [
        migrations.AddField(model_name='savedscript', name='generation_mode',
                            field=models.CharField(max_length=16, default='recording')),
        migrations.AddField(model_name='savedscript', name='generation_context',
                            field=models.JSONField(default=dict)),
    ]
