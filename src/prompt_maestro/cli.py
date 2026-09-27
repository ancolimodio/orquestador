"""CLI: `prompt-maestro run "requerimiento" --repo ./mi-proyecto`."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from prompt_maestro.gates import SandboxGateRunner
from prompt_maestro.llm import AnthropicLLM
from prompt_maestro.models import TaskStatus
from prompt_maestro.observability import EventLog
from prompt_maestro.orchestrator import Orchestrator, OrchestratorConfig
from prompt_maestro.sandbox import CommandRunner, ContainerSandbox, Sandbox
from prompt_maestro.workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-maestro", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Ejecuta una tarea de punta a punta.")
    run.add_argument("requirement", help="Requerimiento en lenguaje natural.")
    run.add_argument("--repo", type=Path, default=Path.cwd(), help="Repositorio de trabajo.")
    run.add_argument("--model", default=os.environ.get("PM_MODEL", "claude-sonnet-5"))
    run.add_argument("--max-attempts", type=int, default=3, help="Reintentos por gate.")
    run.add_argument("--events", type=Path, default=None, help="Archivo JSONL de eventos.")
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


async def _run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Falta la variable de entorno ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    llm = AnthropicLLM(api_key=api_key, model=args.model)
    try:
        orchestrator = Orchestrator(
            llm=llm,
            workspace=Workspace(args.repo),
            gate_runner=SandboxGateRunner(build_runner(args)),
            events=EventLog(args.events),
            config=OrchestratorConfig(max_attempts_per_gate=args.max_attempts),
        )
        report = await orchestrator.run(args.requirement)
    finally:
        await llm.aclose()
    print(report.model_dump_json(indent=2))
    return 0 if report.status is TaskStatus.DONE else 1


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
