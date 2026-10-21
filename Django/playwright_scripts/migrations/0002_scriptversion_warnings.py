from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('playwright_scripts', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='scriptversion', name='warnings',
            field=models.JSONField(default=list),
        ),
    ]
