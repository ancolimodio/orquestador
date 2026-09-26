"""Acceso confinado al repositorio de trabajo."""

import asyncio
from collections.abc import Iterable
from pathlib import Path

from prompt_maestro.errors import GuardrailViolationError
from prompt_maestro.guardrails import ensure_no_secrets, ensure_path_writable, is_secret_file
from prompt_maestro.models import ChangeKind, Plan

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "node_modules",
    }
)


class Workspace:
    """Lee y escribe archivos sin poder salir de la raíz del repo."""

    def __init__(self, root: Path, *, max_file_bytes: int = 200_000) -> None:
        self.root = root.resolve()
        self.max_file_bytes = max_file_bytes

    def _resolve(self, rel_path: str) -> Path:
        path = (self.root / rel_path).resolve()
        if not path.is_relative_to(self.root):
            raise GuardrailViolationError(f"Ruta fuera del repositorio: {rel_path}")
        if is_secret_file(path.name):
            raise GuardrailViolationError(f"Acceso denegado a archivo de secretos: {rel_path}")
        return path

    async def exists(self, rel_path: str) -> bool:
        path = self._resolve(rel_path)
        return await asyncio.to_thread(path.is_file)

    async def read(self, rel_path: str) -> str:
        path = self._resolve(rel_path)
        size = (await asyncio.to_thread(path.stat)).st_size
        if size > self.max_file_bytes:
            raise GuardrailViolationError(f"Archivo demasiado grande para el contexto: {rel_path}")
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def write(self, rel_path: str, content: str, *, allowed_prefixes: Iterable[str]) -> None:
        ensure_path_writable(rel_path, allowed_prefixes)
        ensure_no_secrets(content, where=rel_path)
        path = self._resolve(rel_path)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content, encoding="utf-8")

    async def read_many(self, rel_paths: Iterable[str]) -> dict[str, str]:
        """Lee en paralelo solo los archivos que existen."""
        paths = [p for p in dict.fromkeys(rel_paths)]
        async with asyncio.TaskGroup() as tg:
            checks = {p: tg.create_task(self.exists(p)) for p in paths}
        existing = [p for p, task in checks.items() if task.result()]
        async with asyncio.TaskGroup() as tg:
            reads = {p: tg.create_task(self.read(p)) for p in existing}
        return {p: task.result() for p, task in reads.items()}

    def _walk(self, max_entries: int) -> list[str]:
        files: list[str] = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts) or not path.is_file():
                continue
            if is_secret_file(path.name):
                continue
            files.append(rel.as_posix())
            if len(files) >= max_entries:
                break
        return files

    async def repo_map(self, *, max_entries: int = 500) -> list[str]:
        return await asyncio.to_thread(self._walk, max_entries)

    async def validate_plan(self, plan: Plan) -> list[str]:
        """Gate A: el plan debe apoyarse en archivos y símbolos reales."""
        errors: list[str] = []
        for item in plan.impacted:
            try:
                exists = await self.exists(item.path)
            except GuardrailViolationError as exc:
                errors.append(str(exc))
                continue
            if item.change is ChangeKind.CREATE:
                if exists:
                    errors.append(f"{item.path}: marcado como 'create' pero ya existe.")
                continue
            if not exists:
                errors.append(f"{item.path}: no existe en el repositorio.")
                continue
            content = await self.read(item.path)
            for symbol in item.symbols:
                name = symbol.rsplit(".", 1)[-1]
                if name not in content:
                    errors.append(f"{item.path}: el símbolo '{symbol}' no aparece en el archivo.")
        return errors
