from prompt_maestro.agents.base import Agent
from prompt_maestro.models import Plan


class Planner(Agent):
    role = "planner"

    async def run(
        self, *, task_id: str, requirement: str, repo_map: list[str], feedback: str = ""
    ) -> Plan:
        prompt = (
            f"task_id: {task_id}\n\n<requerimiento>\n{requirement}\n</requerimiento>\n\n"
            "<mapa_del_repo>\n" + "\n".join(repo_map) + "\n</mapa_del_repo>"
        )
        if feedback:
            prompt += f"\n\n<gate_a_rechazo>\n{feedback}\n</gate_a_rechazo>"
        plan = await self._ask(prompt, Plan)
        return plan.model_copy(update={"task_id": task_id})
