import datetime
from uuid import UUID
from typing import Any, Callable, Generic, ParamSpec, TypeVar, overload

_P = ParamSpec("_P")
_R = TypeVar("_R")

class AsyncResult:
    id: UUID
    result: Any
    def __init__(self, id: str | UUID): ...
    def revoke(self): ...

class EagerResult:
    id: UUID
    result: Any
    def revoke(self): ...

class _shared_task(Generic[_P, _R]):
    name: str
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
        ignore_result: bool | None = ...,
        add_to_parent: bool | None = ...,
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
