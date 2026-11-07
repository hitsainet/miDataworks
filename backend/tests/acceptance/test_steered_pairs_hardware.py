"""Steered pairs against a LIVE miLLM (007 FTASKS 10.9). Collected by CI, skipped without one.

Set ``MILLM_HARDWARE_URL`` (for example ``http://millm.example:8000``), with a model loaded, the
SAE ``MILLM_HARDWARE_SAE`` attached and a profile ``MILLM_HARDWARE_PROFILE`` on that SAE. The test
sends one inline one-index set and the profile through the real native path and checks that the
recorded states equal miLLM's headers and that the hashes miDataworks recomputes equal the
reported ones (TV-1..TV-4 pin the algorithm; this pins the live server to it).
"""

from __future__ import annotations

import os

import pytest

URL = os.environ.get("MILLM_HARDWARE_URL")
pytestmark = pytest.mark.skipif(URL is None, reason="no live miLLM (set MILLM_HARDWARE_URL)")


def test_live_headers_match_the_recomputed_hashes() -> None:
    from src.clients.endpoint_caller import EndpointCaller
    from src.services import server_probe
    from src.services.generation import settings_client, steering
    from src.services.generation.generation_call import CallContext, GenerationRequest
    from src.services.generation.run_service import call_for

    assert URL is not None
    sae = os.environ["MILLM_HARDWARE_SAE"]
    profile_name = os.environ["MILLM_HARDWARE_PROFILE"]
    with EndpointCaller(URL + "/v1", None) as caller:
        info = server_probe.detect_server(caller)
        assert info.is_millm and info.resident is not None
        profile = settings_client.profile_by_name(caller, profile_name)
        attached = {a.sae_id: a.layer for a in settings_client.attachments(caller)}
    assert profile is not None and sae in attached
    model = info.resident.name
    request = GenerationRequest(
        0, "hw", [{"role": "user", "content": "Say hello."}], {"max_tokens": 8}, 7
    )
    inline = CallContext(
        base_url=URL + "/v1",
        model=model,
        is_millm=True,
        body_overrides={"steering": {"sae_id": sae, "features": [{"index": 1, "strength": 4.0}]}},
    )
    (result,) = call_for("native").run([request], inline)
    expected = steering.Expected(
        "inline",
        sae_id=sae,
        layer=attached[sae],
        features=((1, 4.0),),
        set_hash=steering.applied_set_hash(sae, [(1, 4.0)]),
    )
    assert steering.check_header(expected, result.steering_header).matched, result.steering_header
    lam = float(profile.get("intensity") or 1.0)
    pairs = [(int(k), float(v) * lam) for k, v in profile["steering"].items()]
    by_profile = CallContext(
        base_url=URL + "/v1", model=model, is_millm=True, body_overrides={"profile": profile_name}
    )
    (result,) = call_for("native").run([request], by_profile)
    parsed = steering.parse_steering_header(result.steering_header)
    assert parsed.items[0].params["hash"] == steering.applied_set_hash(
        str(profile["sae_id"] or sae), pairs
    )
