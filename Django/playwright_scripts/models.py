"""Versioned replay artifacts, separate from LLM and replay execution outcomes."""
import uuid
from django.conf import settings
from django.db import models


class SavedScript(models.Model):
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    testcase = models.ForeignKey('testcases.TestCase', on_delete=models.SET_NULL, null=True)
    source_result = models.OneToOneField('testcases.TestCaseResult', on_delete=models.SET_NULL, null=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    name = models.CharField(max_length=255)
    deleted = models.BooleanField(default=False)
    snapshot = models.JSONField(default=dict)
    events = models.JSONField(default=list)
    generation_mode = models.CharField(max_length=16, default='recording')
    generation_context = models.JSONField(default=dict)
    generation_status = models.CharField(max_length=24, default='recording')
    generation_error = models.TextField(blank=True)
    generation_token = models.UUIDField(default=uuid.uuid4)
    model_id = models.PositiveIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']


class ScriptVersion(models.Model):
    script = models.ForeignKey(SavedScript, on_delete=models.CASCADE, related_name='versions')
    number = models.PositiveIntegerField()
    code = models.TextField()
    parameters = models.JSONField(default=dict)
    issues = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    origin = models.CharField(max_length=16, default='ai')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['script', 'number'], name='unique_saved_script_version')]


class ScriptRun(models.Model):
    version = models.ForeignKey(ScriptVersion, on_delete=models.PROTECT, related_name='runs')
    result = models.OneToOneField('testcases.TestCaseResult', on_delete=models.CASCADE, related_name='script_run')
    parameters = models.JSONField(default=dict)
    headless = models.BooleanField(default=False)
    timeout_seconds = models.PositiveIntegerField(default=600)
    status = models.CharField(max_length=20, default='pending')
    worker_id = models.CharField(max_length=120, blank=True)
    worker_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    lease_token = models.UUIDField(default=uuid.uuid4)
    heartbeat_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ScriptWorker(models.Model):
    worker_id = models.CharField(max_length=120)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    heartbeat_at = models.DateTimeField()
    supports_headed = models.BooleanField(default=True)
    runtime_version = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['worker_id', 'user'], name='unique_script_worker')]


class ScriptBuild(models.Model):
    """Opt-in live generation: observe, generate, validate, bounded repair."""
    script = models.ForeignKey(SavedScript, on_delete=models.CASCADE, related_name='builds')
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    worker_id = models.CharField(max_length=120)
    status = models.CharField(max_length=24, default='pending_observation')
    lease_token = models.UUIDField(default=uuid.uuid4)
    generation_token = models.UUIDField(null=True)
    headless = models.BooleanField(default=False)
    timeout_seconds = models.PositiveIntegerField(default=180)
    attempt = models.PositiveIntegerField(default=0)
    max_repairs = models.PositiveIntegerField(default=2)
    observation = models.JSONField(default=dict)
    last_run = models.ForeignKey(ScriptRun, on_delete=models.SET_NULL, null=True)
    error = models.TextField(blank=True)
    heartbeat_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']
