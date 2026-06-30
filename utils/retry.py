"""Retry decorators with exponential backoff."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from tenacity import (
    AsyncRetrying,
    Retrying,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings

P = ParamSpec("P")
T = TypeVar("T")


def _retry_kwargs() -> dict:
    settings = get_settings()
    return {
        "stop": stop_after_attempt(settings.http_max_retries),
        "wait": wait_exponential(multiplier=settings.http_retry_backoff, min=1, max=30),
        "reraise": True,
    }


def sync_retry(func: Callable[P, T]) -> Callable[P, T]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in Retrying(**_retry_kwargs()):
            with attempt:
                return func(*args, **kwargs)
        raise RuntimeError("Retry exhausted")

    return wrapper


def async_retry(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        async for attempt in AsyncRetrying(**_retry_kwargs()):
            with attempt:
                return await func(*args, **kwargs)
        raise RuntimeError("Retry exhausted")

    return wrapper
