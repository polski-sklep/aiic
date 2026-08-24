"""Shared test helpers.

Deliberately named with a leading underscore so ``unittest discover`` (pattern
``test*.py``) does not try to collect it as a test module.

Everything here exists to keep the suite hermetic: no socket is ever opened, no
API key is ever read from the ambient environment, and every HTTP response the
product code sees is one this file handed it.
"""
from __future__ import annotations

import contextlib
import socket
from collections.abc import Callable, Iterator
from unittest import mock

import httpx


class NetworkAccessError(AssertionError):
    """Raised if a test tries to open a real socket."""


@contextlib.contextmanager
def no_network() -> Iterator[None]:
    """Hard guard: any attempt to reach the network fails loudly.

    ``httpx.MockTransport`` never touches a socket, so a test that is correctly
    mocked passes through this untouched. A test that leaks a real request dies
    here instead of silently hitting a live API.
    """

    def _blocked(*args: object, **kwargs: object) -> None:
        raise NetworkAccessError("test attempted real network access")

    with (
        mock.patch.object(socket.socket, "connect", _blocked),
        mock.patch.object(socket, "create_connection", _blocked),
        mock.patch.object(socket, "getaddrinfo", _blocked),
    ):
        yield


@contextlib.contextmanager
def mock_http(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    """Route every ``httpx.AsyncClient`` built by product code at ``handler``.

    The tool modules construct their own client (``httpx.AsyncClient(timeout=15)``)
    so there is no seam to inject a transport through. Patching the class is the
    seam.
    """
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    with no_network(), mock.patch.object(httpx, "AsyncClient", factory):
        yield


@contextlib.contextmanager
def instant_sleep() -> Iterator[list[float]]:
    """Neutralise ``asyncio.sleep`` and record the delays that were requested.

    The CoinGecko backoff would otherwise make one test take 30 seconds. The
    recorded delays are the assertion target.
    """
    import asyncio

    recorded: list[float] = []

    async def fake_sleep(delay: float, *args: object, **kwargs: object) -> None:
        recorded.append(delay)

    with mock.patch.object(asyncio, "sleep", fake_sleep):
        yield recorded


@contextlib.contextmanager
def settings_override(**values: object) -> Iterator[None]:
    """Temporarily override fields on the cached Settings singleton.

    ``get_settings`` is ``lru_cache``d, so the tools all share one instance.
    Tests must never depend on what happens to be in the ambient ``.env``.
    """
    from app.config import get_settings

    settings = get_settings()
    previous = {key: getattr(settings, key) for key in values}
    try:
        for key, value in values.items():
            object.__setattr__(settings, key, value)
        yield
    finally:
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


def json_response(payload: object, status_code: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    """Handler that always answers with the same JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler
