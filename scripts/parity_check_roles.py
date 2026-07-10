"""resolve_role 결과가 현행 인라인 리졸버와 동일한지 대조하는 일회성 검증."""
from pathlib import Path
from autoagent.config import load_config
from autoagent.roles import load_roles, resolve_role
from autoagent.workflows.routed_impl import model_for_agent, effort_for_agent

cfg = load_config(Path("autoagent.config.json"))
roles = load_roles(Path("."))
cases = []
for task_type in ("backend", "frontend"):
    for risk in ("high", "medium"):
        for agent in ("claude", "codex"):
            route = {"task_type": task_type, "subtype": "db" if risk == "high" else "api", "risk_level": risk}
            req = "migration auth" if risk == "high" else "add endpoint"
            # implementer(mutating=True)로 대조
            rr = resolve_role(roles["implementer"], config=cfg, route=route, request=req, agent=agent, read_only=False)
            assert rr.model == model_for_agent(cfg, agent, route, req, True), (task_type, risk, agent, "model")
            assert rr.effort == effort_for_agent(cfg, agent, route, req, True), (task_type, risk, agent, "effort")
            cases.append((task_type, risk, agent))
print(f"OK: {len(cases)} implementer cases match current resolvers")
