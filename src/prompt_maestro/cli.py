"""CLI: `prompt-maestro run "requerimiento" --repo ./mi-proyecto`."""

import argparse
import asyncio
import os
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prompt_maestro.gates import SandboxGateRunner
from prompt_maestro.llm import AnthropicLLM, HttpLLM, OpenAICompatibleLLM
from prompt_maestro.models import TaskReport, TaskStatus
from prompt_maestro.observability import EventLog
from prompt_maestro.orchestrator import Orchestrator, OrchestratorConfig
from prompt_maestro.project import ProjectConfigError, load_profile
from prompt_maestro.sandbox import CommandRunner, ContainerSandbox, Sandbox
from prompt_maestro.workspace import Workspace


@dataclass(frozen=True, slots=True)
class Provider:
    key_env: str
    base_url: str
    # None: el proveedor no tiene un modelo por defecto confiable y se exige --model.
    default_model: str | None
    # Los planes gratuitos suelen limitar pedidos por minuto: más reintentos y más espera.
    max_retries: int = 3
    backoff_base_s: float = 0.5


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("ANTHROPIC_API_KEY", "https://api.anthropic.com", "claude-sonnet-5"),
    "gemini": Provider(
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-3.8-flash",
        max_retries=6,
        backoff_base_s=2.0,
    ),
    "openai": Provider("OPENAI_API_KEY", "https://api.openai.com/v1/", None),
}


RUNS_DIR = ".prompt-maestro/runs"


def new_run_dir(base: Path, task_id: str) -> Path:
    """Una carpeta por corrida: ordenable por fecha y sin pisar corridas anteriores."""
    run_dir = base / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{task_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-maestro", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Ejecuta una tarea de punta a punta.")
    run.add_argument("requirement", help="Requerimiento en lenguaje natural.")
    run.add_argument("--repo", type=Path, default=Path.cwd(), help="Repositorio de trabajo.")
    run.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        default=os.environ.get("PM_PROVIDER", "anthropic"),
        help="Proveedor del modelo; cada uno lee su key de su propia variable de entorno.",
    )
    run.add_argument(
        "--model",
        default=os.environ.get("PM_MODEL"),
        help="Modelo a usar (por defecto, el del proveedor).",
    )
    run.add_argument(
        "--base-url",
        default=None,
        help="Endpoint del proveedor (por ejemplo, Ollama con --provider openai: "
        "http://localhost:11434/v1/).",
    )
    run.add_argument("--max-attempts", type=int, default=3, help="Reintentos por gate.")
    run.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help=f"Dónde guardar una carpeta por corrida (por defecto, <repo>/{RUNS_DIR}).",
    )
    run.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Archivo JSONL de eventos (por defecto, el de la carpeta de la corrida).",
    )
    run.add_argument(
        "--container-image",
        default=os.environ.get("PM_CONTAINER_IMAGE"),
        help="Corre los gates en contenedores efímeros sin red con esta imagen (recomendado).",
    )
    run.add_argument("--container-runtime", default="docker", help="docker o podman.")
    return parser


def build_runner(args: argparse.Namespace) -> CommandRunner:
    if args.container_image:
        return ContainerSandbox(
            args.repo, image=args.container_image, runtime=(args.container_runtime,)
        )
    print(
        "Aviso: sin --container-image, los tests generados corren en el host sin aislamiento.",
        file=sys.stderr,
    )
    return Sandbox(args.repo)


def build_llm(args: argparse.Namespace, *, api_key: str, model: str) -> HttpLLM:
    provider = PROVIDERS[args.provider]
    llm_class = AnthropicLLM if args.provider == "anthropic" else OpenAICompatibleLLM
    return llm_class(
        api_key=api_key,
        model=model,
        base_url=args.base_url or provider.base_url,
        max_retries=provider.max_retries,
        backoff_base_s=provider.backoff_base_s,
    )


async def _run(args: argparse.Namespace) -> int:
    provider = PROVIDERS[args.provider]
    api_key = os.environ.get(provider.key_env)
    if not api_key:
        print(f"Falta la variable de entorno {provider.key_env}.", file=sys.stderr)
        return 2
    model = args.model or provider.default_model
    if not model:
        print(f"El proveedor '{args.provider}' requiere --model.", file=sys.stderr)
        return 2
    try:
        profile = await asyncio.to_thread(load_profile, args.repo)
    except ProjectConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    task_id = uuid.uuid4().hex[:8]
    run_dir = await asyncio.to_thread(new_run_dir, args.runs_dir or args.repo / RUNS_DIR, task_id)
    print(f"Corrida {task_id}: {run_dir}", file=sys.stderr)
    llm = build_llm(args, api_key=api_key, model=model)
    try:
        orchestrator = Orchestrator(
            llm=llm,
            workspace=Workspace(args.repo),
            gate_runner=SandboxGateRunner(build_runner(args), profile.gate_specs()),
            events=EventLog(args.events or run_dir / "events.jsonl"),
            config=OrchestratorConfig(max_attempts_per_gate=args.max_attempts, profile=profile),
        )
        report = await orchestrator.run(args.requirement, task_id=task_id)
    except Exception:
        # Un error inesperado no produce TaskReport: se deja la traza en la corrida.
        await asyncio.to_thread(
            (run_dir / "error.txt").write_text, traceback.format_exc(), encoding="utf-8"
        )
        raise
    finally:
        await llm.aclose()
    await write_report(run_dir, report)
    print(report.model_dump_json(indent=2))
    return 0 if report.status is TaskStatus.DONE else 1


async def write_report(run_dir: Path, report: TaskReport) -> None:
    await asyncio.to_thread(
        (run_dir / "report.json").write_text, report.model_dump_json(indent=2), encoding="utf-8"
    )


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
