import datetime
import unittest
from unittest import TestCase

from django.test import override_settings
from django.utils import timezone

from django_taskq.models import Retry, Task

try:
    from django.tasks import TaskResultStatus
    from django.tasks.exceptions import (
        TaskResultDoesNotExist,
        TaskResultMismatch,
    )
    from django_taskq import tasks as dtasks

    HAS_DJANGO_TASKS = True
except ImportError:
    HAS_DJANGO_TASKS = False


if HAS_DJANGO_TASKS:

    @dtasks.task
    def dtaskfunc(a, b=None):
        return (a, b)

    @dtasks.task(queue_name="bar")
    def dtaskfunc_queue(a):
        return a

    @dtasks.task(takes_context=True)
    def dtask_with_context(context, a):
        CONTEXT_CALLS.append((context.attempt, a, context.task_result.id))
        return a

    @dtasks.task
    def dtask_retry():
        raise Retry()

    @dtasks.task
    def dtask_raise(exc_type):
        raise exc_type()

    def undecorated_stock_task(a):
        return a


CONTEXT_CALLS: list = []


@unittest.skipUnless(HAS_DJANGO_TASKS, "django.tasks is not available")
class DjangoTasksInterface(TestCase):
    def test_task_attributes(self):
        self.assertEqual(dtaskfunc.name, "dtaskfunc")
        self.assertEqual(dtaskfunc.queue_name, "default")
        self.assertEqual(dtaskfunc.backend, dtasks.TASKQ_BACKEND_ALIAS)

    def test_using_is_immutable(self):
        modified = dtaskfunc.using(queue_name="other")
        self.assertEqual(dtaskfunc.queue_name, "default")
        self.assertEqual(modified.queue_name, "other")

    def test_enqueue_creates_row(self):
        result = dtaskfunc.enqueue(1, b=2)
        row = Task.objects.get(pk=result.id)
        self.assertEqual(row.repr(), "django_taskq.test_tasks.dtaskfunc(1, b=2)")
        self.assertEqual(result.status, TaskResultStatus.READY)
        self.assertEqual(result.attempts, 0)
        self.assertFalse(result.is_finished)
        with self.assertRaises(ValueError):
            result.return_value

    def test_enqueue_roundtrips_pickled_arguments(self):
        timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        result = dtaskfunc.enqueue(timestamp)
        row = Task.objects.get(pk=result.id)
        self.assertEqual(row.args[0], timestamp)

    def test_queue_name_is_stored(self):
        result = dtaskfunc.using(queue_name="other").enqueue(1)
        row = Task.objects.get(pk=result.id)
        self.assertEqual(row.queue, "other")

    def test_decorator_queue_name_is_stored(self):
        result = dtaskfunc_queue.enqueue(1)
        row = Task.objects.get(pk=result.id)
        self.assertEqual(row.queue, "bar")

    def test_run_after_timedelta_sets_execute_at(self):
        result = dtaskfunc.using(run_after=datetime.timedelta(minutes=5)).enqueue(1)
        row = Task.objects.get(pk=result.id)
        self.assertGreater(row.execute_at, timezone.now())

    def test_priority_is_accepted_and_ignored(self):
        first = dtaskfunc.using(priority=-100, queue_name="prio").enqueue(1)
        second = dtaskfunc.using(priority=100, queue_name="prio").enqueue(2)
        # FIFO by enqueue time, no priority reordering
        row = Task.next_task(queue="prio")
        self.assertEqual(row.pk, int(first.id))

    def test_get_result(self):
        result = dtaskfunc.enqueue(1)
        fetched = dtaskfunc.get_result(result.id)
        self.assertEqual(fetched.id, result.id)
        self.assertEqual(fetched.status, TaskResultStatus.READY)
        self.assertEqual(fetched.args, [1])
        self.assertEqual(fetched.kwargs, {})
        with self.assertRaises(TaskResultMismatch):
            dtask_with_context.get_result(result.id)

    def test_get_result_does_not_exist(self):
        with self.assertRaises(TaskResultDoesNotExist):
            dtaskfunc.get_result("12345")

    def test_backend_get_result(self):
        result = dtaskfunc.enqueue(1)
        fetched = dtasks.task_backend.get_result(result.id)
        self.assertEqual(fetched.id, result.id)

    def test_refresh(self):
        result = dtaskfunc.enqueue(1)
        row = Task.objects.get(pk=result.id)
        row.fail(ValueError("broken"))
        self.assertEqual(result.status, TaskResultStatus.READY)
        result.refresh()
        self.assertEqual(result.status, TaskResultStatus.FAILED)
        self.assertEqual(result.attempts, 1)
        self.assertTrue(result.is_finished)
        with self.assertRaises(ValueError):
            result.return_value

    def test_failed_result_exposes_error(self):
        result = dtaskfunc.enqueue(1)
        row = Task.objects.get(pk=result.id)
        try:
            raise ValueError("broken")
        except ValueError as exc:
            row.fail(exc)
        fetched = dtaskfunc.get_result(result.id)
        self.assertEqual(fetched.status, TaskResultStatus.FAILED)
        self.assertEqual(len(fetched.errors), 1)
        self.assertEqual(fetched.errors[0].exception_class, ValueError)
        self.assertIn("ValueError", fetched.errors[0].traceback)

    def test_worker_executes_task(self):
        dtaskfunc.enqueue(1, b=2)
        row = Task.objects.order_by("pk").last()
        row.execute()

    def test_worker_executes_with_context(self):
        del CONTEXT_CALLS[:]
        result = dtask_with_context.using(queue_name="ctx").enqueue(7)
        row = Task.next_task(queue="ctx")
        row.execute()
        self.assertEqual(CONTEXT_CALLS, [(1, 7, result.id)])

    def test_retry_is_reraised_for_the_worker(self):
        dtask_retry.enqueue()
        row = Task.objects.order_by("pk").last()
        with self.assertRaises(Retry):
            row.execute()

    def test_task_self_retry(self):
        with self.assertRaises(Retry):
            dtask_retry.retry()


@unittest.skipUnless(HAS_DJANGO_TASKS, "django.tasks is not available")
class DjangoTasksStockDecorator(TestCase):
    def test_stock_decorator_uses_configured_backend(self):
        from django.tasks import task as stock_task

        with override_settings(
            TASKS={"reserve": {"BACKEND": "django_taskq.tasks.DatabaseBackend"}}
        ):
            stock = stock_task(queue_name="stock", backend="reserve")(
                undecorated_stock_task
            )
            result = stock.enqueue(1)
            row = Task.objects.get(pk=result.id)
            self.assertEqual(row.queue, "stock")
            self.assertEqual(row.func, "django_taskq.test_tasks.undecorated_stock_task")
            self.assertEqual(result.status, TaskResultStatus.READY)
            self.assertEqual(result.backend, "reserve")


@unittest.skipUnless(HAS_DJANGO_TASKS, "django.tasks is not available")
class DjangoTasksEager(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_eager_success(self):
        result = dtaskfunc.enqueue(1, b=2)
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(result.return_value, (1, 2))
        self.assertTrue(result.is_finished)
        self.assertEqual(result.attempts, 1)
        with self.assertRaises(TaskResultDoesNotExist):
            result.refresh()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_eager_failure(self):
        result = dtask_raise.enqueue(ValueError)
        self.assertEqual(result.status, TaskResultStatus.FAILED)
        self.assertEqual(result.errors[0].exception_class, ValueError)
        self.assertTrue(result.is_finished)
        with self.assertRaises(ValueError):
            result.return_value

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(CELERY_TASK_EAGER_PROPAGATES=True)
    def test_eager_propagates(self):
        with self.assertRaises(ValueError):
            dtask_raise.enqueue(ValueError)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_eager_context(self):
        del CONTEXT_CALLS[:]
        result = dtask_with_context.enqueue(7)
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(result.return_value, 7)
        self.assertEqual(CONTEXT_CALLS, [(1, 7, None)])
