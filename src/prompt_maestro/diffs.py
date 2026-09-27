"""Diffs unificados de lo que cambió la tarea, para que el Reviewer vea cada línea tocada."""

import difflib


def unified_diff(path: str, before: str | None, after: str, *, context: int = 3) -> str:
    """Diff de `path`; `before=None` indica un archivo nuevo. Vacío si no cambió nada."""
    lines = difflib.unified_diff(
        (before or "").splitlines(),
        after.splitlines(),
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile=f"b/{path}",
        n=context,
        lineterm="",
    )
    return "\n".join(lines)
