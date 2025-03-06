import datetime
import inspect
from functools import wraps
from typing import Callable
from uuid import UUID, uuid4

from django.conf import settings
from django.utils import timezone

from django_taskq.models import Retry, Task

__all__ = ["shared_task", "Retry", "AsyncResult", "EagerResult"]


class AsyncResult:
    id: UUID

    def __init__(self, id):
        self.id = id

    def revoke(self):
        Task.objects.filter(pk=self.id.int).delete()


class EagerResult:
    id: UUID
    result = None

    def __init__(self, result):
        self.id = uuid4()
        self.result = result


def _funcstr(func: Callable):
    module = inspect.getmodule(func)
    assert module != None
    return ".".join((module.__name__, func.__name__))


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
        eta = timezone.now() + datetime.timedelta(seconds=int(countdown))
    if expires and isinstance(expires, (int, float)):
        expires = timezone.now() + datetime.timedelta(seconds=int(expires))

    args = args or ()
    kwargs = kwargs or {}

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        try:
            return EagerResult(result=func(*args, **kwargs))
        except:
            if getattr(settings, "CELERY_TASK_EAGER_PROPAGATES", False):
                raise
    else:
        task = Task(
            queue=queue,
            func=_funcstr(func),
            args=args,
            kwargs=kwargs,
            execute_at=eta,
            expires_at=expires,
        )
        task.save()
        return AsyncResult(id=UUID(int=task.pk))


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
        _apply_async(self.func, self.args, self.kwargs, **(self.options | kwargs))


def _retry(exc=None, eta=None, countdown=None, max_retries=None):
    if not eta:
        if countdown is None:
            countdown = 3 * 60
        eta = timezone.now() + datetime.timedelta(seconds=int(countdown))
    raise Retry(exc=exc, execute_at=eta, max_retries=max_retries)


def _maybe_wrap_autoretry(
    func: Callable,
    autoretry_for=(),
    dont_autoretry_for=(),
    retry_kwargs={},
    default_retry_delay=3 * 60,
):
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
                _retry(
                    exc=exc,
                    countdown=retry_kwargs.get("countdown", default_retry_delay),
                    max_retries=retry_kwargs.get("max_retries", 3),
                )

        return run

    return func


def shared_task(*args, **kwargs):
    def create_shared_task(**options):
        def run(func):
            queue = options.pop("queue", None)
            func.name = _funcstr(func)
            func.delay = lambda *args, **kwargs: _apply_async(func, args, kwargs)
            func.apply_async = lambda *args, **kwargs: _apply_async(
                func, *args, **(dict(queue=queue) | kwargs)
            )
            func.s = lambda *args, **kwargs: Signature(func, args, kwargs).set(
                queue=queue
            )
            func.retry = lambda *args, **kwargs: _retry(*args, **kwargs)
            return _maybe_wrap_autoretry(func, **options)

        return run

    if len(args) and callable(args[0]):
        return create_shared_task(**kwargs)(args[0])
    return create_shared_task(*args, **kwargs)
