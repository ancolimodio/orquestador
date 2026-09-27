from prompt_maestro.agents.base import Agent, render_files
from prompt_maestro.models import ChangeSet, Plan


class Tester(Agent):
    role = "tester"

    async def run(
        self,
        *,
        plan: Plan,
        implementation: ChangeSet,
        existing_tests: dict[str, str],
        feedback: str = "",
    ) -> ChangeSet:
        code = {c.path: c.content for c in implementation.changes}
        prompt = (
            f"<plan>\n{plan.model_dump_json(indent=2)}\n</plan>\n\n"
            f"<implementacion>\n{render_files(code)}\n</implementacion>\n\n"
            f"<tests_existentes>\n{render_files(existing_tests)}\n</tests_existentes>"
        )
        if feedback:
            prompt += f"\n\n<correcciones_pedidas>\n{feedback}\n</correcciones_pedidas>"
        return await self._ask(prompt, ChangeSet)
