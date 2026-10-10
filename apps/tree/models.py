"""
Models for tree-level administration: configuration values and task results.
"""

from django.db import models


class Config(models.Model):
    """Key/value configuration store (PUT /api/config/<key>/)."""

    key = models.CharField(max_length=100, primary_key=True)
    value = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "gramps_config"

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=None):
        try:
            return cls.objects.get(pk=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value):
        obj, _ = cls.objects.update_or_create(pk=key, defaults={"value": value})
        return obj


class TaskResult(models.Model):
    """
    Result of a (synchronously executed) background-style task.

    The frontend polls GET /api/tasks/<id> until state is one of
    SUCCESS, FAILURE or REVOKED. Since this backend has no task queue,
    tasks run inline and their result is stored here.
    """

    STATE_PENDING = "PENDING"
    STATE_STARTED = "STARTED"
    STATE_SUCCESS = "SUCCESS"
    STATE_FAILURE = "FAILURE"
    STATE_REVOKED = "REVOKED"

    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=100, default="", blank=True)
    state = models.CharField(max_length=20, default=STATE_PENDING)
    result = models.JSONField(null=True, blank=True)
    info = models.JSONField(null=True, blank=True)
    created = models.FloatField(default=0)

    class Meta:
        db_table = "gramps_task_result"

    def __str__(self):
        return f"{self.name} ({self.state})"

    def as_dict(self):
        data = {
            "state": self.state,
            "result": self.result,
            "info": self.info,
            "result_object": self.result,
        }
        return data
