import datetime
import traceback

from django.db import models, transaction
from django.utils import timezone
from picklefield.fields import PickledObjectField


class MaxRetriesExceededError(Exception): ...


class Retry(Exception):
    exc = None
    execute_at = None
    max_retries = None
    backoff = None

    def __init__(self, exc=None, execute_at=None, max_retries=None, backoff: float = 0):
        super().__init__()
        self.exc = exc
        self.execute_at = execute_at
        self.max_retries = max_retries
        self.backoff = backoff


class Task(models.Model):
    DEFAULTQ = "default"
    queue = models.CharField(max_length=64)

    func = models.CharField(max_length=256)
    args = PickledObjectField()
    kwargs = PickledObjectField()

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    execute_at = models.DateTimeField(editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    alive_at = models.DateTimeField(null=True, blank=True, editable=False)

    retries = models.PositiveIntegerField(default=0, editable=False)
    traceback = models.TextField(null=True, blank=True, editable=False)

    started = models.BooleanField(default=False, editable=False)
    failed = models.BooleanField(default=False, editable=False)

    @property
    def error(self):
        if self.traceback:
            return self.traceback.split("\n")[-1]

    def arguments(self):
        arguments = [str(arg) for arg in self.args]
        arguments += [str(key) + "=" + str(value) for key, value in self.kwargs.items()]
        return ", ".join(arguments)

    def repr(self):
        return f"{self.func}({self.arguments()})"

    def __str__(self):
        return f"{self.repr()}#{self.pk}"

    def save(self, *args, **kwargs):
        if len(self.func) > 256:
            raise ValueError("Function name is too long")

        self.args = self.args or ()
        self.kwargs = self.kwargs or {}

        self.queue = self.queue or self.DEFAULTQ
        if len(self.queue) > 64:
            raise ValueError("Queue name is too long")

        if not self.execute_at:
            self.execute_at = timezone.now()
        super().save(*args, **kwargs)

    def fail(self, exc):
        self.started = False
        self.failed = True
        self.traceback = "".join(traceback.format_exception(exc)).strip()
        self.save(update_fields=["started", "failed", "traceback"])

    def retry(self, retry_info: Retry):
        self.retries += 1
        if retry_info.max_retries is not None and self.retries > retry_info.max_retries:
            # TODO: maybe raise?
            if retry_info.exc:
                self.fail(retry_info.exc)
            else:
                self.fail(MaxRetriesExceededError())
            return False

        self.started = False
        self.failed = False
        if retry_info.exc:
            self.traceback = "".join(traceback.format_exception(retry_info.exc)).strip()
        else:
            self.traceback = None

        if retry_info.backoff:
            self.execute_at = timezone.now() + datetime.timedelta(
                seconds=self.retries * retry_info.backoff
            )
        else:
            self.execute_at = retry_info.execute_at
        self.save(
            update_fields=["started", "failed", "traceback", "retries", "execute_at"]
        )
        return True

    def force_retry(self):
        return self.retry(
            Retry(exc=None, execute_at=timezone.now(), max_retries=self.retries + 1)
        )

    def execute(self):
        last_dot = self.func.rindex(".")
        mod = self.func[:last_dot]
        func = self.func[last_dot + 1 :]

        f = getattr(__import__(mod, fromlist=(func,)), func)
        f(*self.args, **self.kwargs)

    @classmethod
    def next_task(cls, queue=None):
        while True:
            with transaction.atomic():
                taskqs = cls.objects.select_for_update(skip_locked=True).filter(
                    failed=False,
                    started=False,
                    execute_at__lte=timezone.now(),
                )
                if queue:
                    taskqs = taskqs.filter(queue=queue)

                task = taskqs.order_by("execute_at").first()
                if not task:
                    return task

                if task.expires_at and task.expires_at <= timezone.now():
                    task.delete()
                else:
                    task.started = True
                    task.alive_at = timezone.now()
                    task.save(update_fields=["started", "alive_at"])
                    return task

    @classmethod
    def alive(cls, task_id):
        cls.objects.filter(pk=task_id).update(alive_at=timezone.now())

    class Meta:
        indexes = [
            models.Index(
                name="taskq_execute_at_queue_idx",
                fields=("execute_at", "queue"),
                condition=models.Q(failed=False, started=False),
            )
        ]


class TaskManager(models.Manager):
    def __init__(self, filter):
        super().__init__()
        self._filter = filter

    def get_queryset(self):
        return super().get_queryset().filter(self._filter()).order_by("execute_at")


class PendingTask(Task):
    objects = TaskManager(
        lambda: models.Q(failed=False, started=False, execute_at__lte=timezone.now())
    )

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        proxy = True


class FutureTask(Task):
    objects = TaskManager(
        lambda: models.Q(failed=False, started=False, execute_at__gt=timezone.now())
    )

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        proxy = True


class ActiveTask(Task):
    objects = TaskManager(
        lambda: models.Q(
            started=True,
            alive_at__gt=timezone.now() - datetime.timedelta(seconds=3),
        )
    )

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        proxy = True


class DirtyTask(Task):
    objects = TaskManager(
        lambda: models.Q(
            started=True,
            alive_at__lte=timezone.now() - datetime.timedelta(seconds=3),
        )
    )

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        proxy = True


class FailedTask(Task):
    objects = TaskManager(lambda: models.Q(failed=True))

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        proxy = True


class TaskSummary(Task):
    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]:
        proxy = True
        verbose_name = "Task Summary"
        verbose_name_plural = "Task Summary"
