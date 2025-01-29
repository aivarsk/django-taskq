import datetime
from unittest import TestCase

from django.utils import timezone

from django_taskq.celery import shared_task
from django_taskq.models import Retry, Task


class TaskTestCase(TestCase):
    def test_repr_is_created_on_save(self):
        task = Task.objects.create(func="foo", args=(1, 2), kwargs={"a": 3, "b": 4})
        self.assertEqual(task.repr, "foo(1, 2, a=3, b=4)")

    def test_datetime_parameter_can_be_serialized(self):
        timestamp = datetime.datetime.now()
        task = Task.objects.create(func="foo", args=(timestamp,), kwargs={})
        task.refresh_from_db()
        self.assertEqual(task.args[0], timestamp)


@shared_task
def dummy(a, b=None):
    del a, b


@shared_task
def failing_task():
    raise KeyError()


@shared_task(autoretry_for=(ValueError,))
def retry_task():
    raise ValueError()


@shared_task
def self_retry_task():
    do_self_retry_because_pyright_does_not_see_retry()


def do_self_retry_because_pyright_does_not_see_retry():
    # Does not see retry in the annotated function itself
    self_retry_task.retry()


@shared_task(autoretry_for=(ValueError,))
def retry_count():
    raise ValueError()


@shared_task(
    autoretry_for=(ValueError,), retry_kwargs={"max_retries": 2, "countdown": 5}
)
def retry_params():
    raise ValueError()


class CeleryInterfaceTestCase(TestCase):
    def test_shared_task_delay(self):
        dummy.delay(1, 2)

        task = Task.objects.last()
        self.assertEqual(task.repr, "django_taskq.tests.dummy(1, 2)")

    def test_shared_task_apply_async(self):
        dummy.apply_async(args=(1, 2))

        task = Task.objects.last()
        self.assertEqual(task.repr, "django_taskq.tests.dummy(1, 2)")

    def test_shared_task_signature_delay(self):
        dummy.s(1, 2).delay()

        task = Task.objects.last()
        self.assertEqual(task.repr, "django_taskq.tests.dummy(1, 2)")

    def test_shared_task_signature_apply_async(self):
        dummy.s(1, 2).apply_async()

        task = Task.objects.last()
        self.assertEqual(task.repr, "django_taskq.tests.dummy(1, 2)")

    def test_shared_task_fail(self):
        failing_task.delay()

        task = Task.objects.last()
        with self.assertRaises(KeyError):
            task.execute()

    def test_shared_task_retry(self):
        retry_task.delay()

        task = Task.objects.last()
        with self.assertRaises(Retry):
            task.execute()

    def test_shared_task_self_retry(self):
        self_retry_task.delay()

        task = Task.objects.last()
        with self.assertRaises(Retry):
            task.execute()

    def test_shared_task_self_retry_counter(self):
        retry_count.delay()

        task = Task.objects.last()
        task.execute_at = timezone.now()
        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertFalse(task.failed)
        self.assertAlmostEqual(
            (task.execute_at - timezone.now()).seconds, 3 * 60, delta=1
        )

        task.execute_at = timezone.now()
        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertFalse(task.failed)
        self.assertAlmostEqual(
            (task.execute_at - timezone.now()).seconds, 3 * 60, delta=1
        )

        task.execute_at = timezone.now()
        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertFalse(task.failed)
        self.assertAlmostEqual(
            (task.execute_at - timezone.now()).seconds, 3 * 60, delta=1
        )

        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertTrue(task.failed)

    def test_shared_task_self_retry_params(self):
        retry_params.delay()

        task = Task.objects.last()
        task.execute_at = timezone.now()
        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertFalse(task.failed)
        self.assertAlmostEqual((task.execute_at - timezone.now()).seconds, 5, delta=1)

        task.execute_at = timezone.now()
        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertFalse(task.failed)
        self.assertAlmostEqual((task.execute_at - timezone.now()).seconds, 5, delta=1)

        with self.assertRaises(Retry) as exc_info:
            task.execute()
        task.retry(exc_info.exception)
        self.assertTrue(task.failed)
