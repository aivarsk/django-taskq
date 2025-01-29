import datetime
import inspect
from functools import wraps
from typing import Callable

from django.conf import settings
from django.utils import timezone

from django_taskq.models import Retry, Task

__all__ = ["shared_task", "Retry"]


def _apply_async(
    func: Callable,
    args: tuple | None = None,
    kwargs: dict | None = None,
    countdown: float | None = None,
    eta: datetime.datetime | None = None,
    expires: float | datetime.datetime | None = None,
    queue: str | None = None,
):
    if countdown:
        eta = timezone.now() + timezone.timedelta(seconds=int(countdown))
    if expires and isinstance(expires, (int, float)):
        expires = timezone.now() + timezone.timedelta(seconds=int(expires))

    module = inspect.getmodule(func)
    assert module != None
    funcstr = ".".join((module.__name__, func.__name__))

    task = Task(
        queue=queue,
        func=funcstr,
        args=args or (),
        kwargs=kwargs or {},
        execute_at=eta,
        expires_at=expires,
    )
    task.save()

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        try:
            task.execute()
            task.delete()
        except Retry as retry:
            task.retry(retry)
        except Exception as exc:
            task.fail(exc)


class Signature:
    def __init__(self, func, args, kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.options = {}

    def set(self, **kwargs):
        self.options.update(kwargs)
        return self

    def delay(self):
        _apply_async(self.func, self.args, self.kwargs, **self.options)

    def apply_async(self, **kwargs):
        self.options.update(kwargs)
        _apply_async(self.func, self.args, self.kwargs, **self.options)


def _retry(exc=None, eta=None, countdown=None, max_retries=None):
    if not eta:
        if countdown is None:
            countdown = 3 * 60
        eta = timezone.now() + timezone.timedelta(seconds=int(countdown))
    raise Retry(exc=exc, execute_at=eta, max_retries=max_retries)


def _maybe_wrap_autoretry(
    func: Callable,
    autoretry_for=(),
    dont_autoretry_for=(),
    retry_kwargs={},
):
    max_retries = retry_kwargs.get("max_retries", 3)
    countdown = retry_kwargs.get("countdown", 3 * 60)
    eta = timezone.now() + timezone.timedelta(seconds=int(countdown))

    if autoretry_for:

        @wraps(func)
        def run(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Retry:
                raise
            except dont_autoretry_for:
                raise
            except autoretry_for as exc:
                raise Retry(exc=exc, execute_at=eta, max_retries=max_retries)

        return run

    return func


def shared_task(*args, **kwargs):
    def create_shared_task(**options):
        def run(func):
            func.delay = lambda *args, **kwargs: _apply_async(func, args, kwargs)
            func.apply_async = lambda *args, **kwargs: _apply_async(
                func, *args, **kwargs
            )
            func.s = lambda *args, **kwargs: Signature(func, args, kwargs)
            func.retry = lambda *args, **kwargs: _retry(*args, **kwargs)
            return _maybe_wrap_autoretry(func, **options)

        return run

    if len(args) and callable(args[0]):
        return create_shared_task(**kwargs)(args[0])
    return create_shared_task(*args, **kwargs)
