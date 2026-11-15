"""
Django Tasks API (``django.tasks``) backed by the django_taskq engine.

This module is a bridge, not a re-implementation. Task definition and
result lookup wear the Django Tasks shape; execution, retries, and
scheduling stay with the engine. Properties that belong to other
systems (result payloads live in your models, chaining is a state
machine) or cannot be solved correctly anyway (priorities, per-attempt
history) are not emulated.

What carries over from the engine:

- Tasks are rows in the ``taskq_task`` table. Enqueue is a single INSERT,
  so it participates in the surrounding transaction and is rolled back
  together with it. To enqueue after commit, wrap ``enqueue()`` in
  ``transaction.on_commit()`` exactly like the Celery-style API requires.
- Arguments and return values are pickled, not JSON-serialized:
  datetimes, model instances, and other picklable objects work.
- Retries use the engine's ``Retry`` exception (raise it, or call
  ``task.retry()`` from inside the task), with backoff, jitter, and
  ``max_retries`` handled by the worker.
- The worker deletes completed tasks, so the queue cleans itself up.
  As a consequence a ``SUCCESSFUL`` status is never persisted: once a
  task finishes successfully its result is gone and ``get_result()``
  raises ``TaskResultDoesNotExist``.
- ``settings.CELERY_TASK_ALWAYS_EAGER`` executes tasks inline on
  ``enqueue()`` (``CELERY_TASK_EAGER_PROPAGATES`` propagates exceptions),
  same as the Celery-style API.

Contract mapping:

============================ ============================================
Django Tasks                 django_taskq engine
============================ ============================================
``backend.enqueue()``        INSERT into ``taskq_task``
``queue_name``               ``Task.queue``
``run_after``                ``Task.execute_at`` (timedelta or datetime)
``get_result()``             lookup of the pending/failed row
``status`` READY             pending row
``status`` RUNNING           ``started=True`` row
``status`` FAILED            ``failed=True`` row
``status`` SUCCESSFUL        never stored (row deleted by the worker)
``enqueued_at``              ``Task.created_at``
``last_attempted_at``        ``Task.alive_at`` (heartbeat, approximate)
``attempts``                 ``Task.retries``, best-effort snapshot
============================ ============================================

Usage::

    from django_taskq.tasks import task

    @task(queue_name="emails")
    def email_users(emails, subject, message):
        ...

    result = email_users.enqueue(["user@example.com"], subject="Hi", message="...")
    result.status  # TaskResultStatus.READY

    # defer
    email_users.using(run_after=datetime.timedelta(minutes=5)).enqueue(...)

To enqueue after the current transaction commits, wrap ``enqueue()`` in
``transaction.on_commit()`` as usual.

The backend is also usable through the stock Django decorator by pointing
the ``TASKS`` setting at it::

    TASKS = {"default": {"BACKEND": "django_taskq.tasks.DatabaseBackend"}}

    from django.tasks import task  # stock decorator, our engine

Django >= 6.0 is required; on older versions install the ``django-tasks``
backport package, which provides the same ``django.tasks`` namespace.
"""

import datetime
import pickle
import traceback
from dataclasses import dataclass
from typing import ClassVar

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

try:
    from django.tasks import task_backends
    from django.tasks.base import (
        Task,
        TaskContext,
        TaskError,
        TaskResult,
        TaskResultStatus,
    )
    from django.tasks.backends.base import BaseTaskBackend
    from django.tasks.exceptions import (
        InvalidTask,
        TaskResultDoesNotExist,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "django_taskq.tasks requires Django 6.0+ "
        "(or the 'django-tasks' backport package on older versions)"
    ) from exc

from django_taskq.celery import _retry
from django_taskq.models import Retry
from django_taskq.models import Task as TaskRow

__all__ = ["task", "DatabaseBackend", "DatabaseTask", "DatabaseTaskResult", "task_backend"]

TASKQ_BACKEND_ALIAS = "taskq"


@dataclass(frozen=True, slots=True, kw_only=True)
class DatabaseTask(Task):
    """A django.tasks Task that can execute itself on a taskq worker."""

    # Marker checked by TaskRow.execute() to route execution here without
    # importing this module.
    _taskq_database_task: ClassVar[bool] = True

    def retry(self, *args, **kwargs):
        """Self-retry, mirroring the Celery-style API: raises Retry."""
        return _retry(*args, **kwargs)

    def _taskq_execute(self, row):
        """Called by TaskRow.execute() when the row's function is a DatabaseTask."""
        result = self.get_backend().result_for_row(row, self)
        context = TaskContext(task_result=result)
        if self.takes_context:
            self.func(context, *row.args, **row.kwargs)
        else:
            self.func(*row.args, **row.kwargs)


@dataclass(frozen=True, slots=True, kw_only=True)
class DatabaseTaskResult(TaskResult):
    """A django.tasks TaskResult snapshot of a queue table row.

    Unlike Django's JSON-backed contract, arguments are presented exactly
    as stored: this engine round-trips them through pickle, so datetimes,
    model instances, and other picklable objects survive on the result.
    ``refresh()`` is inherited from the base class.
    """

    retries: int = 0

    def __post_init__(self):
        # Skip TaskResult.__post_init__(), which normalizes args/kwargs
        # through JSON and rejects pickled values such as datetimes.
        pass

    @property
    def attempts(self):
        # Best effort: the engine tracks retries, not per-attempt
        # history. Ready results report completed attempts, running and
        # failed ones report the current attempt number.
        return self.retries + (1 if self.status != TaskResultStatus.READY else 0)


class DatabaseBackend(BaseTaskBackend):
    """django.tasks backend storing Tasks in the django_taskq queue table."""

    supports_defer = True
    supports_get_result = True
    # The worker drains queues FIFO ordered by execute_at.
    supports_priority = False
    # The worker is synchronous.
    supports_async_task = False

    task_class = DatabaseTask

    def __init__(self, alias, params):
        super().__init__(alias, params)
        if "QUEUES" not in params:
            # Queue names are created on demand; only an explicit QUEUES
            # setting restricts them.
            self.queues = set()

    def validate_task(self, task):
        # Priority is accepted but ignored: the worker drains queues FIFO.
        # Django 6.0's validate_task() also assumes run_after is a datetime
        # and crashes on timedelta. Neutralize both for the check.
        changes = {}
        if task.priority != 0:
            changes["priority"] = 0
        if isinstance(task.run_after, datetime.timedelta):
            changes["run_after"] = timezone.now() + task.run_after
        if not changes:
            return super().validate_task(task)
        original = {key: getattr(task, key) for key in changes}
        for key, value in changes.items():
            object.__setattr__(task, key, value)
        try:
            super().validate_task(task)
        finally:
            for key, value in original.items():
                object.__setattr__(task, key, value)

    def enqueue(self, task, args, kwargs):
        # Validate and normalize arguments up front. Pickle (not JSON)
        # round-trip: anything unpicklable fails here, at enqueue time.
        args, kwargs = pickle.loads(pickle.dumps((tuple(args), dict(kwargs))))

        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            return self._execute_eager(task, args, kwargs)

        execute_at = task.run_after
        if isinstance(execute_at, datetime.timedelta):
            execute_at = timezone.now() + execute_at

        row = TaskRow.objects.create(
            queue=task.queue_name,
            func=task.module_path,
            args=args,
            kwargs=kwargs,
            execute_at=execute_at,
        )
        return self.result_for_row(row, task)

    def get_result(self, result_id):
        try:
            row = TaskRow.objects.get(pk=int(result_id))
        except (TaskRow.DoesNotExist, ValueError, TypeError, OverflowError):
            raise TaskResultDoesNotExist(result_id) from None
        return self.result_for_row(row, self.task_for_row(row))

    def task_for_row(self, row):
        try:
            func = import_string(row.func)
        except ImportError:
            # The task function no longer exists; the row is orphaned.
            raise TaskResultDoesNotExist(row.pk) from None
        if getattr(func, "_taskq_database_task", False):
            # The module attribute is the Task instance created by the
            # decorator; the original function is on it.
            func = func.func
        return self.task_class(
            priority=0,
            func=func,
            queue_name=row.queue,
            backend=self.alias,
            takes_context=False,
            run_after=None,
        )

    def result_for_row(self, row, task):
        status = (
            TaskResultStatus.FAILED
            if row.failed
            else TaskResultStatus.RUNNING
            if row.started
            else TaskResultStatus.READY
        )
        errors = []
        if row.failed and row.traceback:
            errors = [
                TaskError(
                    exception_class_path=self._exception_class_path(row.traceback),
                    traceback=row.traceback,
                )
            ]
        return DatabaseTaskResult(
            task=task,
            id=str(row.pk),
            status=status,
            enqueued_at=row.created_at,
            started_at=None,
            finished_at=None,
            last_attempted_at=row.alive_at,
            args=list(row.args),
            kwargs=dict(row.kwargs),
            backend=self.alias,
            errors=errors,
            worker_ids=[],
            retries=row.retries,
        )

    def _execute_eager(self, task, args, kwargs):
        now = timezone.now()
        result = DatabaseTaskResult(
            task=task,
            id=None,
            status=TaskResultStatus.RUNNING,
            enqueued_at=now,
            started_at=now,
            finished_at=None,
            last_attempted_at=None,
            args=list(args),
            kwargs=dict(kwargs),
            backend=self.alias,
            errors=[],
            worker_ids=[],
            retries=0,
        )
        context = TaskContext(task_result=result)
        try:
            if task.takes_context:
                return_value = task.func(context, *args, **kwargs)
            else:
                return_value = task.func(*args, **kwargs)
        except Retry:
            # The worker handles retries; in eager mode there is no worker.
            raise
        except Exception as exc:
            if getattr(settings, "CELERY_TASK_EAGER_PROPAGATES", False):
                raise
            object.__setattr__(result, "status", TaskResultStatus.FAILED)
            object.__setattr__(result, "finished_at", timezone.now())
            object.__setattr__(
                result,
                "errors",
                [
                    TaskError(
                        exception_class_path=f"{type(exc).__module__}.{type(exc).__qualname__}",
                        traceback="".join(traceback.format_exception(exc)).strip(),
                    )
                ],
            )
            return result

        object.__setattr__(result, "status", TaskResultStatus.SUCCESSFUL)
        object.__setattr__(result, "finished_at", timezone.now())
        object.__setattr__(result, "_return_value", return_value)
        return result

    @staticmethod
    def _exception_class_path(traceback_string):
        # The engine stores only the formatted traceback, so recover the
        # exception path from its last line: "ValueError: ..." or a dotted
        # "module.ExcClass: ...".
        last_line = traceback_string.strip().splitlines()[-1]
        name = last_line.split(":", 1)[0].strip()
        return name if "." in name else f"builtins.{name}"


def task(
    function=None,
    *,
    priority=0,
    queue_name="default",
    backend=TASKQ_BACKEND_ALIAS,
    takes_context=False,
    **kwargs,
):
    """Define a Task, same signature as django.tasks.task().

    Defaults to this package's backend; pass ``backend`` to enqueue onto
    another configured ``TASKS`` backend instead.
    """

    def wrapper(f):
        return task_backends[backend].task_class(
            priority=priority,
            func=f,
            queue_name=queue_name,
            backend=backend,
            takes_context=takes_context,
            run_after=None,
            **kwargs,
        )

    if function:
        return wrapper(function)
    return wrapper


# The always-available backend for django_taskq.tasks.task, registered
# under its own alias so it never hijacks the "default" TASKS backend.
task_backend = DatabaseBackend(alias=TASKQ_BACKEND_ALIAS, params={})
task_backends[TASKQ_BACKEND_ALIAS] = task_backend
