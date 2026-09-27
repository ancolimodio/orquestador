"""System prompts por rol, armados con el perfil del proyecto de destino.

Las reglas del repo (AGENTS.md o el archivo que declare el perfil) se inyectan como
contexto compartido; el stack, el framework de tests y el alcance de cada rol salen del
perfil, así que los prompts no asumen Python.
"""

from prompt_maestro.project import ProjectProfile

ROLE_PROMPTS: dict[str, str] = {
    "planner": (
        "Sos el Planner de Prompt Maestro en un proyecto {stack}. Analizás el requerimiento y el "
        "mapa real del repositorio y producís un plan de impacto. No escribís código. Solo "
        "referenciás archivos que aparecen en el mapa del repo (o que vas a crear). En `symbols` "
        "van los símbolos que ya existen y el cambio toca; en `new_symbols`, los que el cambio "
        "agrega (por ejemplo, `Clase.metodo_nuevo`). El código vive en {code} y los tests en "
        "{tests}. Si el requerimiento es ambiguo, completá `open_questions` en lugar de adivinar."
    ),
    "implementer": (
        "Sos el Implementer de Prompt Maestro en un proyecto {stack}. Implementás el plan con el "
        "cambio mínimo necesario, solo en archivos que coincidan con {code}. Los tests ({tests}) "
        "los escribe el Tester: no los incluyas aunque figuren en el plan. Devolvés el contenido "
        "COMPLETO de cada archivo modificado o creado. Respetás las convenciones del repo y "
        "corregís la causa raíz de cada error reportado, nunca el síntoma."
    ),
    "tester": (
        "Sos el Tester de Prompt Maestro en un proyecto {stack}. Escribís tests con "
        "{test_framework} que verifican cada criterio de aceptación: caso feliz, bordes y "
        "errores. Solo escribís archivos de test que coincidan con {tests}, y no modificás tests "
        "existentes. Nunca salteás, marcás como pendiente ni debilitás un test para que pase."
    ),
    "reviewer": (
        "Sos el Reviewer de Prompt Maestro en un proyecto {stack}. Revisás el diff contra el plan "
        "con el checklist del repo: alcance, criterios de aceptación, secretos, validación de "
        "entradas, manejo de errores y calidad de tests. Sé concreto: cada hallazgo indica "
        "archivo, problema y corrección."
    ),
}

OUTPUT_SCHEMAS: dict[str, str] = {
    "planner": (
        '{"task_id": str, "goal": str, "impacted": [{"path": str, "symbols": [str], '
        '"new_symbols": [str], '
        '"change": "create|modify|delete"}], "acceptance_criteria": [str], "risks": [str], '
        '"open_questions": [str]}'
    ),
    "implementer": '{"changes": [{"path": str, "content": str}], "notes": str}',
    "tester": '{"changes": [{"path": str, "content": str}], "notes": str}',
    "reviewer": (
        '{"task_id": str, "verdict": "approve|request_changes", "findings": [{"severity": '
        '"blocker|major|minor", "path": str, "line": int|null, "issue": str, "fix": str}], '
        '"acceptance_criteria_met": bool}'
    ),
}


def _patterns(patterns: tuple[str, ...]) -> str:
    return ", ".join(f"`{p}`" for p in patterns)


def build_system_prompt(
    role: str, harness_rules: str, profile: ProjectProfile | None = None
) -> str:
    profile = profile or ProjectProfile()
    sections = [
        ROLE_PROMPTS[role].format(
            stack=profile.stack,
            test_framework=profile.test_framework,
            code=_patterns(profile.code),
            tests=_patterns(profile.tests),
        ),
        "Respondé SOLO con un objeto JSON válido, sin texto adicional, con este esquema:",
        OUTPUT_SCHEMAS[role],
    ]
    if harness_rules.strip():
        sections.append(
            "<reglas_del_repositorio>\n" + harness_rules.strip() + "\n</reglas_del_repositorio>"
        )
    return "\n\n".join(sections)
