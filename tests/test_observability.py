from prompt_maestro.observability import Event, EventLog


async def test_phase_durations_sums_by_phase() -> None:
    log = EventLog()
    await log.emit(
        Event(
            task_id="t-1",
            agent="implementer",
            phase="implement",
            action="run_check",
            result="pass",
            duration_ms=120,
        )
    )
    await log.emit(
        Event(
            task_id="t-1",
            agent="implementer",
            phase="implement",
            action="run_check",
            result="pass",
            duration_ms=80,
        )
    )
    await log.emit(
        Event(
            task_id="t-1",
            agent="tester",
            phase="test",
            action="run_tests",
            result="pass",
            duration_ms=300,
        )
    )

    assert log.phase_durations() == {
        "implement": 200,
        "test": 300,
    }


async def test_phase_durations_ignores_events_without_duration() -> None:
    log = EventLog()
    await log.emit(
        Event(
            task_id="t-1",
            agent="planner",
            phase="plan",
            action="start",
            result="ok",
            duration_ms=None,
        )
    )
    await log.emit(
        Event(
            task_id="t-1",
            agent="implementer",
            phase="implement",
            action="edit",
            result="ok",
            duration_ms=150,
        )
    )
    await log.emit(
        Event(
            task_id="t-1",
            agent="implementer",
            phase="implement",
            action="check",
            result="ok",
        )
    )

    assert log.phase_durations() == {"implement": 150}


def test_phase_durations_empty_log() -> None:
    log = EventLog()
    assert log.phase_durations() == {}

    log.events.append(
        Event(
            task_id="t-1",
            agent="planner",
            phase="plan",
            action="start",
            result="ok",
            duration_ms=None,
        )
    )
    assert log.phase_durations() == {}
