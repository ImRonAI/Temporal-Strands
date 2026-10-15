"""Claim the session's desktop before its first browser/computer-use tool call.

Strands ``HookRegistry.invoke_callbacks_async`` awaits coroutine callbacks, so
this hook can run the provisioning activity inline in workflow context. The
workflow owns the binding and the lock; this hook only decides *when*.

Import this module through ``workflow.unsafe.imports_passed_through()``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeToolCallEvent


class DesktopHook(HookProvider):
    def __init__(self, desktop_tool_names: Collection[str], ensure: Callable[[], Awaitable[Any]]) -> None:
        self._names = frozenset(desktop_tool_names)
        self._ensure = ensure

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)

    async def _before(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] in self._names:
            await self._ensure()
