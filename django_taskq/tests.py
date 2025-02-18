import datetime
import logging
from unittest import TestCase

from django.utils import timezone

from django_taskq.celery import shared_task
from django_taskq.models import Retry, Task


class TaskTestCase(TestCase):
    def test_repr_is_created_on_save(self):
        task = Task.objects.create(func="foo", args=(1, 2), kwargs={"a": 3, "b": 4})
        self.assertEqual(task.repr(), "foo(1, 2, a=3, b=4)")

    def test_datetime_parameter_can_be_serialized(self):
        timestamp = datetime.datetime.now()
        task = Task.objects.create(func="foo", args=(timestamp,), kwargs={})
        task.refresh_from_db()
        self.assertEqual(len(task.args), 1)
        self.assertEqual(task.args[0], timestamp)


@shared_task
def taskfunc(a, b=None):
    del a, b


class CeleryInterfaceBasicCalls(TestCase):
    def _assert_last_task(self, r):
        task = Task.objects.last()
        assert task is not None
        self.assertEqual(task.repr(), r)
        self.assertAlmostEqual(
            task.execute_at, timezone.now(), delta=datetime.timedelta(seconds=1)
        )

    def test_shared_task_with_delay_args(self):
        taskfunc.delay(1, 2)
        self._assert_last_task("django_taskq.tests.taskfunc(1, 2)")

    def test_shared_task_with_delay_args_kwargs(self):
        taskfunc.delay(1, b=2)
        self._assert_last_task("django_taskq.tests.taskfunc(1, b=2)")

    def test_shared_task_with_delay_kwargs(self):
        taskfunc.delay(a=1, b=2)
        self._assert_last_task("django_taskq.tests.taskfunc(a=1, b=2)")

    def test_shared_task_apply_async_args(self):
        taskfunc.apply_async(args=(1, 2))
        self._assert_last_task("django_taskq.tests.taskfunc(1, 2)")

    def test_shared_task_apply_async_args_kwargs(self):
        taskfunc.apply_async(args=(1,), kwargs={"b": 2})
        self._assert_last_task("django_taskq.tests.taskfunc(1, b=2)")

    def test_shared_task_apply_async_kwargs(self):
        taskfunc.apply_async(kwargs={"a": 1, "b": 2})
        self._assert_last_task("django_taskq.tests.taskfunc(a=1, b=2)")

    def test_shared_task_signature_delay_args(self):
        taskfunc.s(1, 2).delay()
        self._assert_last_task("django_taskq.tests.taskfunc(1, 2)")

    def test_shared_task_signature_delay_args_kwargs(self):
        taskfunc.s(1, b=2).delay()
        self._assert_last_task("django_taskq.tests.taskfunc(1, b=2)")

    def test_shared_task_signature_delay_kwargs(self):
        taskfunc.s(a=1, b=2).delay()
        self._assert_last_task("django_taskq.tests.taskfunc(a=1, b=2)")

    def test_shared_task_signature_apply_async_args(self):
        taskfunc.s(1, 2).apply_async()
        self._assert_last_task("django_taskq.tests.taskfunc(1, 2)")

    def test_shared_task_signature_apply_async_args_kwargs(self):
        taskfunc.s(1, b=2).apply_async()
        self._assert_last_task("django_taskq.tests.taskfunc(1, b=2)")

    def test_shared_task_signature_apply_async_kwargs(self):
        taskfunc.s(a=1, b=2).apply_async()
        self._assert_last_task("django_taskq.tests.taskfunc(a=1, b=2)")


class CeleryInterfaceApplyAsync(TestCase):
    def _assert_last_task(self, eta, expires):
        task = Task.objects.last()
        assert task is not None
        self.assertAlmostEqual(
            task.execute_at, eta, delta=datetime.timedelta(seconds=1)
        )
        self.assertAlmostEqual(
            task.expires_at, expires, delta=datetime.timedelta(seconds=1)
        )

    def test_shared_task_apply_async(self):
        taskfunc.apply_async(args=(1, 2))
        self._assert_last_task(eta=timezone.now(), expires=None)

    def test_shared_task_apply_async_countdown(self):
        taskfunc.apply_async(args=(1, 2), countdown=10)
        self._assert_last_task(
            eta=timezone.now() + datetime.timedelta(seconds=10), expires=None
        )

    def test_shared_task_apply_async_eta(self):
        eta = timezone.now() + datetime.timedelta(seconds=60)
        taskfunc.apply_async(args=(1, 2), eta=eta)
        self._assert_last_task(eta=eta, expires=None)

    def test_shared_task_apply_async_expires(self):
        expires = timezone.now() + datetime.timedelta(seconds=60)
        taskfunc.apply_async(args=(1, 2), expires=expires)
        self._assert_last_task(eta=timezone.now(), expires=expires)

    def test_shared_task_apply_async_expires_float(self):
        taskfunc.apply_async(args=(1, 2), expires=60)
        self._assert_last_task(
            eta=timezone.now(), expires=timezone.now() + datetime.timedelta(seconds=60)
        )


@shared_task(queue="bar")
def taskfunc_queue(a, b=None):
    del a, b


class CeleryInterfaceQueue(TestCase):
    def _assert_last_task(self, queue):
        task = Task.objects.last()
        assert task is not None
        self.assertEqual(task.queue, queue)

    def test_shared_task_apply_async(self):
        taskfunc.apply_async(args=(1, 2))
        self._assert_last_task(queue=Task.DEFAULTQ)

    def test_shared_task_apply_async_queue(self):
        taskfunc.apply_async(args=(1, 2), queue="foo")
        self._assert_last_task(queue="foo")

    def test_shared_task_queue_apply_async(self):
        taskfunc_queue.apply_async(args=(1, 2))
        self._assert_last_task(queue="bar")

    def test_shared_task_queue_apply_async_queue(self):
        taskfunc_queue.apply_async(args=(1, 2), queue="foo")
        self._assert_last_task(queue="foo")

    def test_shared_task_queue_s_apply_async(self):
        taskfunc_queue.s(1, 2).apply_async()
        self._assert_last_task(queue="bar")

    def test_shared_task_queue_s_apply_async_queue(self):
        taskfunc_queue.s(1, 2).apply_async(queue="foo")
        self._assert_last_task(queue="foo")


@shared_task
def task_with_raise(exc_type):
    raise exc_type()


@shared_task(autoretry_for=(KeyError,))
def task_with_raise_autoretry_KeyError(exc_type):
    raise exc_type()


@shared_task(autoretry_for=(KeyError,), dont_autoretry_for=(ValueError,))
def task_with_raise_autoretry_KeyError_dont_ValueError(exc_type):
    raise exc_type()


class CeleryInterfaceAutoretry(TestCase):
    def _assert_task_raises(self, exc_type):
        task = Task.objects.last()
        assert task is not None
        with self.assertRaises(exc_type):
            task.execute()

    def test_failing_with_exception(self):
        task_with_raise.delay(KeyError)
        self._assert_task_raises(KeyError)

    def test_failing_and_autoretry(self):
        task_with_raise_autoretry_KeyError.delay(KeyError)
        self._assert_task_raises(Retry)

    def test_failing_and_autoretry_for_different_exception(self):
        task_with_raise_autoretry_KeyError.delay(ValueError)
        self._assert_task_raises(ValueError)

    def test_failing_and_autoretry_with_dont(self):
        task_with_raise_autoretry_KeyError.delay(KeyError)
        self._assert_task_raises(Retry)

    def test_failing_and_autoretry_with_dont_matching(self):
        task_with_raise_autoretry_KeyError.delay(ValueError)
        self._assert_task_raises(ValueError)

    def test_failing_and_autoretry_with_dont_not_matching(self):
        task_with_raise_autoretry_KeyError.delay(AttributeError)
        self._assert_task_raises(AttributeError)


@shared_task
def task_self_retry(**kwargs):
    task_self_retry.retry(**kwargs)


@shared_task
def task_self_retry_indirect():
    do_self_retry_because_pyright_does_not_see_retry()


def do_self_retry_because_pyright_does_not_see_retry():
    # Does not see retry in the annotated function itself
    task_self_retry_indirect.retry()


class CeleryInterfaceManualRetry(TestCase):
    def _assert_task_retries(self, execute_at, max_retries):
        task = Task.objects.last()
        assert task is not None
        with self.assertRaises(Retry) as exc_info:
            task.execute()
        retry = exc_info.exception
        self.assertAlmostEqual(
            retry.execute_at, execute_at, delta=datetime.timedelta(seconds=1)
        )
        self.assertEqual(retry.max_retries, max_retries)

    def test_failing_with_exception_with_autoretry(self):
        task_self_retry.delay()
        self._assert_task_retries(
            execute_at=timezone.now() + datetime.timedelta(seconds=3 * 60),
            max_retries=None,
        )

    def test_failing_with_exception_with_autoretry_indirect(self):
        task_self_retry_indirect.delay()
        self._assert_task_retries(
            execute_at=timezone.now() + datetime.timedelta(seconds=3 * 60),
            max_retries=None,
        )

    def test_failing_with_exception_with_autoretry_eta(self):
        eta = timezone.now() + datetime.timedelta(seconds=10)
        task_self_retry.delay(eta=eta)
        self._assert_task_retries(
            execute_at=eta,
            max_retries=None,
        )

    def test_failing_with_exception_with_autoretry_countdown(self):
        task_self_retry.delay(countdown=60)
        self._assert_task_retries(
            execute_at=timezone.now() + datetime.timedelta(seconds=60),
            max_retries=None,
        )


@shared_task(retry_kwargs={"max_retries": 2})
def task_with_retry_params():
    pass


@shared_task
def task_info():
    logging.info("This is info")


@shared_task
def task_debug():
    logging.debug("This is debug")


class RetryExecution(TestCase):
    def test_task_logging(self):
        task_info.delay()
        task_debug.delay()

    def _test_a_retry(self, retry, retries, failed=False):
        task = Task.objects.last()
        assert task is not None
        task.retry(retry)
        task.refresh_from_db()
        self.assertEqual(task.failed, failed)
        self.assertEqual(task.retries, retries)
        if not task.failed:
            # Not updated for exhausted retries
            self.assertEqual(task.execute_at, retry.execute_at)

    def test_with_max_retries(self):
        task_with_retry_params.delay()
        self._test_a_retry(Retry(execute_at=timezone.now(), max_retries=2), 1)
        self._test_a_retry(Retry(execute_at=timezone.now(), max_retries=2), 2)
        self._test_a_retry(
            Retry(execute_at=timezone.now(), max_retries=2), 2, failed=True
        )

    def test_without_max_retries(self):
        task_with_retry_params.delay()
        self._test_a_retry(Retry(execute_at=timezone.now()), 1)
        self._test_a_retry(Retry(execute_at=timezone.now()), 2)
        self._test_a_retry(Retry(execute_at=timezone.now()), 3)
