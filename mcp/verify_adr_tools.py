"""Prove atlas_adr_search and atlas_adr_get are pass-throughs (ADR 0045).

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp` package.
What the routes answer (the id scheme, the 409, visibility) is proven against a real Postgres
by backend/verify_adr_records.py.

    python mcp/verify_adr_tools.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeClient, FakeMCP, check, failures, tools_read
from app.envelope import AtlasError  # noqa: E402


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_read.client = fake_client
    tools_read.register(fake_mcp)
    search, get = fake_mcp.tools.get("atlas_adr_search"), fake_mcp.tools.get("atlas_adr_get")

    print("-- A. registered --")
    check("both tools are registered", search is not None and get is not None, sorted(fake_mcp.tools))
    if search is None or get is None:
        sys.exit(1)

    print("-- B. search: one GET /api/adrs, only the filters asked for --")
    fake_client.payload = {"total": 1, "items": [{"id": "AT001", "project": "atlas"}]}
    out = search(q="identity")
    path, params = fake_client.calls[-1]
    check("GET /api/adrs with q only", path == "/api/adrs" and params == {"q": "identity"},
          (path, params))
    check("the answer is the API's, unreshaped", out == {"ok": True, "result": fake_client.payload}, out)
    search(project="atlas", status="accepted", number=41)
    check("project, status and number pass through",
          fake_client.calls[-1][1] == {"project": "atlas", "status": "accepted", "number": 41},
          fake_client.calls[-1])

    print("-- C. get: one GET /api/adrs/{id}, project only when given --")
    get(id="AT001")
    check("GET /api/adrs/AT001, no params", fake_client.calls[-1] == ("/api/adrs/AT001", {}),
          fake_client.calls[-1])
    get(id="0012", project="ai-dev-seat")
    check("a legacy number with project=", fake_client.calls[-1]
          == ("/api/adrs/0012", {"project": "ai-dev-seat"}), fake_client.calls[-1])

    print("-- D. an API refusal is the envelope's error --")
    fake_client.raises = AtlasError("conflict", "ADR 0012 is held by 2 projects")
    out = get(id="0012")
    check("ok is False and the message is kept",
          out.get("ok") is False and "2 projects" in str(out.get("error", {}).get("message")), out)

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
