import datetime
import inspect
from functools import wraps
from uuid import UUID, uuid4

from django.conf import settings
from django.utils import timezone

from django_taskq.models import Retry, Task

__all__ = ["shared_task", "Retry", "AsyncResult", "EagerResult"]


class AsyncResult:
    def __init__(self, id, result=None):
        self.id = UUID(hex=id) if isinstance(id, str) else id
        self.result = result

    def revoke(self):
        Task.objects.filter(
            pk=self.id.int,
            failed=False,
            started=False,
        ).delete()


class EagerResult(AsyncResult):
    def revoke(self):
        pass


def _funcstr(func):
    module = inspect.getmodule(func)
    assert module != None
    return ".".join((module.__name__, func.__name__))


def _apply_async(
    func,
    args=None,
    kwargs=None,
    countdown=None,
    eta=None,
    expires=None,
    queue=None,
    ignore_result=None,
    add_to_parent=None,
):
    # nop
    ignore_result = ignore_result
    add_to_parent = add_to_parent

    if countdown:
        eta = timezone.now() + datetime.timedelta(seconds=int(countdown))
    if expires and isinstance(expires, (int, float)):
        expires = timezone.now() + datetime.timedelta(seconds=int(expires))

    args = args or ()
    kwargs = kwargs or {}

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        try:
            return EagerResult(uuid4(), result=func(*args, **kwargs))
        except:
            if getattr(settings, "CELERY_TASK_EAGER_PROPAGATES", False):
                raise
    else:
        task = Task.objects.create(
            queue=queue,
            func=_funcstr(func),
            args=args,
            kwargs=kwargs,
            execute_at=eta,
            expires_at=expires,
        )
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


def _retry(
    exc=None,
    eta=None,
    countdown=None,
    max_retries=3,
    retry_backoff=False,
    retry_backoff_max=600,
    retry_jitter=True,
    default_retry_delay=3 * 60,
):
    countdown = countdown or default_retry_delay
    if not eta:
        eta = timezone.now() + datetime.timedelta(seconds=int(countdown))

    if retry_backoff and isinstance(retry_backoff, bool):
        retry_backoff = 2

    raise Retry(
        exc=exc,
        execute_at=eta,
        max_retries=max_retries,
        backoff=retry_backoff,
        backoff_max=retry_backoff_max,
        jitter=retry_jitter,
    )


def _maybe_wrap_autoretry(
    func,
    autoretry_for=(),
    dont_autoretry_for=(),
    retry_kwargs={},
    **options,
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
                    **retry_kwargs,
                    **options,
                )

        return run

    return func


def shared_task(*args, **kwargs):
    def create_shared_task(**options):
        def run(func):
            queue = options.pop("queue", None)
            func.name = _funcstr(func)
            func.delay = lambda *args, **kwargs: _apply_async(
                func, args, kwargs, **(dict(queue=queue))
            )
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
