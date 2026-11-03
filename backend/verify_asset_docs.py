"""
Verification for `app.undocumented_assets` — the drift check that answers
"is every module the board serves mentioned in any node document at all".

No pytest suite exists in this repo (see verify_object_graph.py's docstring),
so this follows the standalone run-it-and-read-it convention against a real,
disposable Postgres via `testdb` (TASK-1044).

WHAT THIS PROVES, and what it deliberately does not.

The check exists because `atlas-board-shell`'s architecture document carries a
hand-maintained mermaid graph of the board's modules, and on 2026-08-24 it
named seven of ten — `mine.js` and `settings.js` had shipped months earlier,
`compose.js` that same day. Nothing complained. So the mechanism under test is
"ask the corpus", and the thing that would make it worthless is a check that
passes because the fixture happens to be clean.

THREE SECTIONS:

  A. THE MECHANISM, both directions. A module mentioned in a document is not
     reported; the same module with its mention removed IS reported. Proving
     only the happy direction would leave a check that cannot fail — the
     convention `backend/TESTING.md` names explicitly.

  B. SCOPE. Vendored third-party payloads and the favicon are served but not
     authored here, so they are never reported however empty the corpus is.
     The asset list is DERIVED from `app.ASSET_FILES`, never pinned to a
     hand-written list — a pinned list would be a second census to keep true,
     which is the very failure this check exists to catch.

  C. IT IS A WARNING, NOT A GATE. An empty corpus reports every first-party
     asset rather than raising, and `/api/health` carries the field for a
     human to read. A signal that is always red is one people learn to ignore;
     a signal that fails a build on a fresh database is worse.

Run (from a venv with the backend requirements installed):
    python verify_asset_docs.py
"""
from __future__ import annotations

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault(
    "ATLAS_FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

import app  # noqa: E402
from models_graph import Node, NodeDoc  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def first_party() -> list[str]:
    """Derived exactly as the implementation derives it — never pinned."""
    return sorted(
        name for name, path in app.ASSET_FILES.items()
        if not path.startswith(app.VENDOR_PREFIX) and not name.endswith(".ico")
    )


def set_corpus(text: str | None, kind: str = "architecture") -> None:
    """Replace the whole node_doc corpus with one document, or empty it.

    The document is REGISTERED in its node's `docs[]` with the given kind,
    because an unregistered row is neither served nor counted — writing the
    row alone would test a state the board never actually has.
    """
    leaf = "architecture.md" if kind == "architecture" else "log.md"
    path = "docs/nodes/doc-host/" + leaf
    with app.session() as s:
        for row in s.scalars(app.select(NodeDoc)).all():
            s.delete(row)
        host = s.get(Node, "doc-host")
        if host is None:
            host = Node(id="doc-host", type="capability", name="doc-host",
                        attributes={}, attention_count=0, completion=0.0,
                        origin="atlas")
            s.add(host)
        host.attributes = {} if text is None else {
            "docs": [{"kind": kind, "title": "doc-host", "path": path}]
        }
        s.flush()
        if text is not None:
            s.add(NodeDoc(node_id="doc-host", path=path, content=text))
        s.commit()


def report() -> list[str]:
    with app.session() as s:
        return app.undocumented_assets(s)


def main() -> None:
    app.startup()  # real startup: create_all + schema reconcile + node_type seed
    assets = first_party()
    print(f"\n=== A. the mechanism, both directions ({len(assets)} first-party assets) ===")

    check("there is something to check at all", len(assets) >= 2,
          f"first-party assets: {assets}")

    # Every first-party asset named in one document -> nothing reported.
    set_corpus("Modules: " + ", ".join(assets))
    check("an asset named in a document is NOT reported", report() == [],
          f"reported {report()}")

    # Board v2 reuses legacy basenames (`v2/core/api.js` beside `api.js`). A
    # document naming only the v2 path must not document the legacy module, or
    # the warning goes silent for exactly the file nobody described.
    shadowed = sorted(n for n in assets if "/" not in n and any(a.endswith("/" + n) for a in assets))
    if shadowed:
        legacy = shadowed[0]
        set_corpus("Modules: " + ", ".join(a for a in assets if a != legacy))
        got = report()
        check(f"a v2 path ending in {legacy!r} does not document the legacy {legacy!r}",
              got == [legacy], f"reported {got}")
        set_corpus("Modules: frontend/" + legacy + ", " + ", ".join(a for a in assets if a != legacy))
        check("...while a plain mention of it, even under frontend/, still does", report() == [],
              f"reported {report()}")

    # Drop exactly one mention. That one, and only that one, must come back.
    dropped = assets[0]
    set_corpus("Modules: " + ", ".join(a for a in assets if a != dropped))
    got = report()
    check("removing one mention reports exactly that asset", got == [dropped],
          f"dropped {dropped!r}, reported {got}")
    check("the check can fail — it is not vacuous", got != [],
          "the same corpus minus one mention changed the answer")

    print("\n=== A2. a NON-architecture document does not count ===")
    # THE REGRESSION THIS SECTION EXISTS FOR. The first version of the check
    # asked "mentioned in ANY node document", and the live corpus defeated it
    # immediately: mine.js and compose.js read as documented because they are
    # named in atlas-run-log's run-log.md, which this project's own loop
    # tooling publishes on every run. The check was reading its own exhaust.
    set_corpus("Modules: " + ", ".join(assets), kind="runbook")
    got_runbook = report()
    check("a runbook naming every asset documents NONE of them",
          sorted(got_runbook) == assets,
          f"reported {len(got_runbook)} of {len(assets)}")
    set_corpus("Modules: " + ", ".join(assets), kind="architecture")
    check("the same text in an architecture document DOES document them",
          report() == [], "same content, different kind, opposite answer")

    print("\n=== B. scope: vendored payloads and the favicon are never reported ===")
    set_corpus(None)  # empty corpus: the worst case for over-reporting
    everything = report()
    vendored = [n for n, p in app.ASSET_FILES.items() if p.startswith(app.VENDOR_PREFIX)]
    icons = [n for n in app.ASSET_FILES if n.endswith(".ico")]
    check("no vendored payload is reported, even with an empty corpus",
          all(v not in everything for v in vendored), f"vendored: {vendored}")
    check("no favicon is reported, even with an empty corpus",
          all(i not in everything for i in icons), f"icons: {icons}")
    check("every first-party asset IS reported when nothing documents them",
          sorted(everything) == assets, f"reported {len(everything)} of {len(assets)}")

    print("\n=== C. it is a warning, not a gate ===")
    # An empty corpus is the state of every fresh database. It must produce a
    # list, not an exception, and /api/health must still answer.
    check("an empty corpus returns a list rather than raising",
          isinstance(everything, list))
    with app.session() as s:
        doc_rows = s.scalar(app.select(app.func.count()).select_from(NodeDoc))
    check("the corpus really was empty for that case", doc_rows == 0,
          f"node_doc rows: {doc_rows}")

    # The field is derived, so a new module added to ASSET_FILES tomorrow is
    # covered without anyone editing this test.
    set_corpus("Modules: " + ", ".join(assets))
    check("a fully-documented corpus is the clean case", report() == [])

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    try:
        main()
    finally:
        DB.stop()
