from prompt_maestro.agents.base import Agent, render_files
from prompt_maestro.models import ChangeSet, Plan


class Implementer(Agent):
    role = "implementer"

    async def run(self, *, plan: Plan, files: dict[str, str], feedback: str = "") -> ChangeSet:
        prompt = (
            f"<plan>\n{plan.model_dump_json(indent=2)}\n</plan>\n\n"
            f"<archivos_actuales>\n{render_files(files)}\n</archivos_actuales>"
        )
        if feedback:
            prompt += f"\n\n<errores_a_corregir>\n{feedback}\n</errores_a_corregir>"
        return await self._ask(prompt, ChangeSet)
