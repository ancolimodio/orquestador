from prompt_maestro.agents.base import Agent, render_files
from prompt_maestro.models import ChangeSet, GateResult, Plan, Review


class Reviewer(Agent):
    role = "reviewer"

    async def run(
        self,
        *,
        plan: Plan,
        implementation: ChangeSet,
        tests: ChangeSet,
        static_analysis: GateResult,
    ) -> Review:
        files = {c.path: c.content for c in [*implementation.changes, *tests.changes]}
        analysis = "sin hallazgos" if static_analysis.passed else static_analysis.failure_report()
        prompt = (
            f"<plan>\n{plan.model_dump_json(indent=2)}\n</plan>\n\n"
            f"<diff>\n{render_files(files)}\n</diff>\n\n"
            f"<analisis_estatico>\n{analysis}\n</analisis_estatico>"
        )
        review = await self._ask(prompt, Review)
        return review.model_copy(update={"task_id": plan.task_id})
