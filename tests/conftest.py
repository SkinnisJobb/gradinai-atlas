"""pytest wiring for the install smoke (tests/mcp_contract, tests/e2e). Needs a live OSS install;
see tests/atlas_smoke.py for the environment it reads."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_smoke as smoke  # noqa: E402


@pytest.fixture(scope="session")
def owner() -> smoke.Session:
    return smoke.signed_in(smoke.OWNER)


@pytest.fixture(scope="session")
def ids(owner: smoke.Session) -> dict:
    return {u: smoke.user_id(owner, u) for u in (smoke.OWNER, smoke.MEMBER)}


@pytest.fixture(scope="session")
def shared_project(owner: smoke.Session, ids: dict) -> str:
    """alice owner, bob editor: where both agents write in the attribution test, and what the
    Playwright story is bound to."""
    smoke.ensure_project(owner, smoke.SHARED_PROJECT, "Smoke shared project",
                         [(ids[smoke.OWNER], "owner"), (ids[smoke.MEMBER], "editor")])
    return smoke.SHARED_PROJECT
