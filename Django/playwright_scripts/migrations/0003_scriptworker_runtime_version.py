from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('playwright_scripts', '0002_scriptversion_warnings')]
    operations = [migrations.AddField(
        model_name='scriptworker', name='runtime_version',
        field=models.PositiveIntegerField(default=0))]
