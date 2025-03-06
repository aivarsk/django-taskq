import datetime
from typing import Any, Callable, Generic, ParamSpec, TypeVar, overload

from django_taskq.celery import AsyncResult, EagerResult

_P = ParamSpec("_P")
_R = TypeVar("_R")

class _shared_task(Generic[_P, _R]):
    @staticmethod
    def delay(*args: _P.args, **kwargs: _P.kwargs) -> AsyncResult | EagerResult: ...
    @staticmethod
    def s(*args: _P.args, **kwargs: _P.kwargs): ...
    @staticmethod
    def apply_async(
        args: tuple[Any, ...] | None = ...,
        kwargs: dict[str, Any] | None = ...,
        eta: datetime.datetime | None = ...,
        countdown: float | None = ...,
        expires: float | datetime.datetime | None = ...,
        queue: str | None = ...,
    ) -> AsyncResult | EagerResult: ...
    @staticmethod
    def retry(
        exc: Exception | None = ...,
        eta: datetime.datetime | None = ...,
        countdown: float | None = ...,
        max_retries: int | None = ...,
    ): ...

@overload
def shared_task(func: Callable[_P, _R]) -> _shared_task[_P, _R]: ...
@overload
def shared_task(
    *,
    queue: str = ...,
    autoretry_for: tuple[type[BaseException], ...] = ...,
    dont_autoretry_for: tuple[type[BaseException], ...] = ...,
    retry_kwargs: dict[str, Any] = ...,
    default_retry_delay: float | None = ...,
) -> Callable[[Callable[_P, _R]], _shared_task[_P, _R]]: ...
