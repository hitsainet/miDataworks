"""What a download step's ``commit_check`` says, in one place (011 FR-011.7, FR-011.8; D37).

- ``verified``: miStudio 035 recorded the publish commit for the dataset it downloaded or reused;
- ``claimed``: miStudio did not pin downloads when the step ran, so the commit is miDataworks'
  claim (FR-009.21, FR-009.24 contain the risk);
- NULL: the step was written before feature 011.

REST (``send_service.step_out``, the results ``commits``), the MCP results tools and the UI badge
all read the phrase from here; a send's recorded state is never upgraded after the fact.
"""

from __future__ import annotations

VERIFIED = "verified"
CLAIMED = "claimed"

PHRASES: dict[str | None, str] = {
    VERIFIED: "verified by miStudio",
    CLAIMED: "claimed (miStudio does not pin downloads)",
    None: "not checked (sent before feature 011)",
}


def phrase_of(value: str | None) -> str:
    return PHRASES[value]
