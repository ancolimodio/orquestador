"""Eventos estructurados de cada acción de los agentes."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class Event(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    task_id: str
    agent: str
    phase: str
    action: str
    result: str
    attempt: int = 1
    duration_ms: int | None = None
    detail: str = ""


class EventLog:
    """Guarda eventos en memoria y, opcionalmente, en un archivo JSON Lines."""

    def __init__(self, sink: Path | None = None) -> None:
        self.events: list[Event] = []
        self._sink = sink
        self._lock = asyncio.Lock()

    @staticmethod
    def _append_line(sink: Path, line: str) -> None:
        with sink.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    async def emit(self, event: Event) -> Event:
        async with self._lock:
            self.events.append(event)
            if self._sink is not None:
                await asyncio.to_thread(self._append_line, self._sink, event.model_dump_json())
        return event

    def metrics(self) -> dict[str, int]:
        """Rechazos por gate: sirven para mejorar el harness, no solo el modelo."""
        rejections: dict[str, int] = {}
        for ev in self.events:
            if ev.action == "gate" and ev.result == "fail":
                rejections[ev.phase] = rejections.get(ev.phase, 0) + 1
        return rejections
