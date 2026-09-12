"""Event bus for cross-cutting event publication.

Decouples event producers (agents, orchestrator) from consumers
(SSE stream, CLI printer, audit log). Publish/subscribe pattern.

Migrated from: agentic_sar/agent_service.py _emit() function
"""

from __future__ import annotations

import logging
from typing import Callable

from sar.contracts.events import Event, EventType

logger = logging.getLogger(__name__)


class EventBus:
    """Publish/subscribe event dispatcher.

    Producers call emit(). Consumers register via subscribe().
    Handlers that raise exceptions are logged and skipped to prevent
    one faulty subscriber from breaking the entire event pipeline.

    Example:
        bus = EventBus()
        bus.subscribe(EventType.PIPELINE_START, my_handler)
        bus.emit(Event(event_type=EventType.PIPELINE_START, data={"query": "bots"}))
    """

    def __init__(self) -> None:
        """Initialize with empty subscriber registry."""
        self._subscribers: dict[EventType | None, list[Callable[[Event], None]]] = {}

    def subscribe(
        self,
        event_type: EventType | None,
        handler: Callable[[Event], None],
    ) -> None:
        """Register a handler for a specific event type.

        Args:
            event_type: The event type to listen for. Pass None to subscribe
                to ALL event types (wildcard).
            handler: Callback invoked with the Event when emitted.
        """
        self._subscribers.setdefault(event_type, []).append(handler)

    def emit(self, event: Event) -> None:
        """Publish an event to all registered handlers.

        Dispatches the event to handlers registered for its specific type,
        then to wildcard (None) handlers. Exceptions in handlers are logged
        but do not propagate.

        Args:
            event: The event to broadcast.
        """
        for handler in self._subscribers.get(event.event_type, []):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Handler %r failed for event %s", handler, event.event_type
                )

        for handler in self._subscribers.get(None, []):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Wildcard handler %r failed for event %s",
                    handler,
                    event.event_type,
                )

    def clear(self) -> None:
        """Remove all subscribers. Used in tests."""
        self._subscribers.clear()
