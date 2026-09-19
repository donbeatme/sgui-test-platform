import uuid
from django.conf import settings
from django.db import models


class WorkbenchSettings(models.Model):
    project = models.OneToOneField('projects.Project', on_delete=models.CASCADE)
    rules = models.JSONField(default=list)
    prompts = models.JSONField(default=dict)
    stages = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class GenerationJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=200, default='用例草稿')
    status = models.CharField(max_length=24, default='queued')
    stage = models.CharField(max_length=80, default='等待处理')
    input = models.JSONField(default=dict)
    options = models.JSONField(default=dict)
    cases = models.JSONField(default=list)
    issues = models.JSONField(default=dict)
    notes = models.JSONField(default=list)
    progress = models.JSONField(default=dict)
    run_token = models.UUIDField(default=uuid.uuid4, editable=False)
    saved_ids = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class CaseExtension(models.Model):
    case = models.OneToOneField('testcases.TestCase', on_delete=models.CASCADE, related_name='guicase_extension')
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    source_key = models.CharField(max_length=64)
    original = models.JSONField(default=dict)
    job = models.ForeignKey(GenerationJob, null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'source_key'], name='guicase_project_source_unique')]
