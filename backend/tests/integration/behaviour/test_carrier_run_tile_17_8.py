"""A carrier run's tile facts (017 FTASKS 17.8 (c), (d); ENH-006 D94).

- (c) the operator saw "Template: not recorded" on a carrier run: its templates are in
  ``template_map`` (plan value -> template id), not ``respond_template_id``. The run now lists each
  plan value's template by name.
- (d) a carrier run takes miLLM's lease for ITS OWN generator (``generator_model_id``), not the
  generation role's model. The other carrier fixtures name the same model in both, so they could
  not tell the two apart; here the role names another model, as production's did (role
  Qwen2.5-7B-Instruct, generator Llama-3.1-8B-Instruct).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.integration.behaviour.test_carrier_modes import (
    INSTRUCTED_PROMPT,
    INSTRUCTED_SYSTEM,
    run_body,
    started,
    template_map,
)
from tests.integration.behaviour.test_carrier_plan_build import plan_version
from tests.support.generation_fixtures import GEN_MODEL, Gen, set_role
from tests.support.operator_build_driver import RealDriver

LEASE = "/api/models/7/lease"


async def test_a_carrier_run_names_its_templates_and_leases_its_own_generator(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 8)
    mapping = await template_map(
        client, plan, {"system": INSTRUCTED_SYSTEM, "prompt": INSTRUCTED_PROMPT}, "tile"
    )
    set_role("generation", gen.base_url, "role-model-not-the-generator")
    run = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert run["generation_endpoint"]["model_id"] == "role-model-not-the-generator"
    assert run["generator_model_id"] == GEN_MODEL

    # (c) every plan value's template by name, ids as recorded in template_map.
    names = {
        t["value"]: (t["template_id"], t["template"]["name"]) for t in run["carrier_templates"]
    }
    assert names == {g: (tid, f"tile-{g}") for g, tid in mapping["templates"].items()}
    assert [t["value"] for t in run["carrier_templates"]] == sorted(mapping["templates"])

    # (d) the lease: taken once for the resident generator, sent on every request, released.
    done = gen.run_until_done(run["id"])
    assert done.state == "completed" and done.pinned is True
    takes = gen.fake.calls(LEASE, "POST")
    assert len(takes) == 1, [c.path for c in gen.fake.requests]
    chats = gen.chats()
    assert chats and {c.body["model"] for c in chats} == {GEN_MODEL}
    lease_ids = {c.headers.get("x-millm-lease") for c in chats}
    assert len(lease_ids) == 1 and None not in lease_ids
    assert all(c.headers.get("x-millm-load-policy") == "refuse" for c in chats)
    assert gen.fake.calls(LEASE, "DELETE"), "the lease was not released at the end"
