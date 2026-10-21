from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('testcases', '0023_execution_tracking')]
    operations = [
        migrations.AlterField(
            model_name='testexecution', name='status',
            field=models.CharField(
                choices=[('pending', '等待中'), ('running', '执行中'), ('completed', '已完成'),
                         ('skipped', '未触发／不适用'), ('failed', '失败'), ('cancelled', '已取消'),
                         ('blocked', '阻塞'), ('error', '系统错误')],
                default='pending', max_length=20, verbose_name='执行状态'),
        ),
    ]
