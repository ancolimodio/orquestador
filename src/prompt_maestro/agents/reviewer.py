from prompt_maestro.agents.base import Agent
from prompt_maestro.models import GateResult, Plan, Review


class Reviewer(Agent):
    role = "reviewer"

    async def run(self, *, plan: Plan, diff: str, static_analysis: GateResult) -> Review:
        # Un diff unificado, no los archivos completos: en un archivo de cientos de líneas
        # reescrito entero, un cambio fuera del plan solo se ve si se muestran las líneas tocadas.
        analysis = "sin hallazgos" if static_analysis.passed else static_analysis.failure_report()
        prompt = (
            f"<plan>\n{plan.model_dump_json(indent=2)}\n</plan>\n\n"
            f"<diff>\n{diff or '(sin cambios)'}\n</diff>\n\n"
            f"<analisis_estatico>\n{analysis}\n</analisis_estatico>"
        )
        review = await self._ask(prompt, Review)
        return review.model_copy(update={"task_id": plan.task_id})
