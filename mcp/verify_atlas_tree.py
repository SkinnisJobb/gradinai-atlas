"""Prove atlas_tree's minimal defaults, and that it enforces nothing itself (TASK-1163).

No network, no database, no `mcp` package: the tool module is imported with a
stub env, registered on a fake `mcp` that just collects the functions, and its
`client` is replaced with one that records the params and returns a payload
this script builds. What the API does with those params -- the tiers, and the
`strict`/`max_bytes` refusals -- is proven against a real Postgres, in CI, by
backend/verify_object_graph.py §7i2-7i3. This script proves only what this
facade CHOOSES to ask for, and that it holds no limit of its own.

    python mcp/verify_atlas_tree.py      # needs only httpx installed

Same convention as backend/verify_*.py: one [PASS]/[FAIL] line per claim,
exit 1 if any failed.
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import tools_read  # noqa: E402
from app.envelope import AtlasError  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeClient:
    def __init__(self):
        self.calls = []
        self.payload = {"nodes": [], "edges": [], "truncated": False}
        self.raises = None

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.raises:
            raise self.raises
        return self.payload


def minimal_payload(n_nodes: int) -> dict:
    """A realistic default-tier response: the fields `work=none&meta=none&
    prose=none` leaves, with ids as long as this estate's real ones."""
    nodes = [{"id": f"atlas-capability-node-{i:03d}", "type": "capability",
              "name": f"A capability node called {i}", "attention": "grey",
              "intent_count": 0, "status": None} for i in range(n_nodes)]
    edges = [{"id": 1000 + i, "src": nodes[0]["id"], "dst": nodes[i]["id"],
              "edge_type": "contains", "directed": True} for i in range(1, n_nodes)]
    return {"focus": nodes[0]["id"], "depth": 1, "edge_types": ["contains"],
            "nodes": nodes, "edges": edges, "truncated": False}


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_read.client = fake_client
    tools_read.register(fake_mcp)
    tree = fake_mcp.tools["atlas_tree"]

    print("-- A. focus is required --")
    out = tree()
    check("no focus is refused", out.get("ok") is False, str(out)[:120])
    check("the refusal is invalid_request and names atlas_outline",
          out.get("error", {}).get("type") == "invalid_request"
          and "atlas_outline" in out.get("error", {}).get("message", ""))
    check("a refused call never reaches the API", fake_client.calls == [], str(fake_client.calls))

    print("-- B. defaults are the minimum --")
    fake_client.payload = minimal_payload(3)
    out = tree(focus="atlas-backend")
    path, params = fake_client.calls[-1]
    check("focus-only call succeeds", out.get("ok") is True, str(out)[:120])
    check("it reads /api/graph, never the whole-world /api/graph/tree", path == "/api/graph", path)
    check("defaults: depth=1 edges=contains prose/work/meta=none, and asks the API to "
          "refuse past the node and byte limits",
          params == {"focus": "atlas-backend", "depth": 1, "edges": "contains",
                     "prose": "none", "work": "none", "meta": "none",
                     "cap": tools_read.TREE_NODE_LIMIT, "strict": 1,
                     "max_bytes": tools_read.TREE_BYTE_LIMIT}, str(params))
    check("a result within limits is passed through untouched",
          out.get("result") is fake_client.payload)

    print("-- C. every extra is opt-in and passed through --")
    tree(focus="x", depth=2, edges="all", prose="summary", work="items", meta=True)
    _, params = fake_client.calls[-1]
    check("edges=all sends no edges filter", "edges" not in params, str(params))
    check("depth/prose/work pass through",
          (params["depth"], params["prose"], params["work"]) == (2, "summary", "items"), str(params))
    check("meta=true asks for meta=full", params["meta"] == "full", str(params))
    tree(focus="x", depth=0)
    check("depth=0 is honoured, not replaced by the default", fake_client.calls[-1][1]["depth"] == 0)

    print("-- D. the API's refusal reaches the caller as too_large --")
    fake_client.raises = AtlasError("too_large", "GET /api/graph -> 413 too_large: Refused rather "
                                                 "than cut off: narrow depth or edges")
    out = tree(focus="atlas", depth=3, edges="all")
    fake_client.raises = None
    check("a 413 from atlas-api comes back as error type too_large",
          out.get("error", {}).get("type") == "too_large", str(out)[:120])
    check("no partial result is returned", "result" not in out)

    print("-- E. this facade enforces nothing itself --")
    fat = minimal_payload(5)
    fat["truncated"] = True
    for n in fat["nodes"]:
        n["abstract"] = "x" * tools_read.TREE_BYTE_LIMIT
    fake_client.payload = fat
    out = tree(focus="atlas", prose="full")
    check("whatever the API returns is passed through, even truncated and over the byte limit "
          "-- the limits are the API's, not a second copy here",
          out.get("ok") is True and out.get("result") is fat, str(out)[:120])

    print("-- F. the two limits agree with each other --")
    at_limit = minimal_payload(tools_read.TREE_NODE_LIMIT)
    size = len(json.dumps(at_limit, separators=(",", ":")).encode("utf-8"))
    check("a full NODE_LIMIT neighbourhood at the defaults fits under BYTE_LIMIT",
          size < tools_read.TREE_BYTE_LIMIT, f"{size} B vs {tools_read.TREE_BYTE_LIMIT}")

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
