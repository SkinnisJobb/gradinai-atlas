#!/usr/bin/env python3
"""A CycloneDX SBOM for the JavaScript the board vendors (STORY-atlas-lcm-plan, item 1).

The board has no build step and no package manager (ADR 0043): its third-party code is the
minified files in frontend/vendor/, served as they are. No scanner can name a minified bundle,
so this reads each file's own version banner, writes a CycloneDX 1.5 document with an npm purl
per library, and Trivy then scans that SBOM (`trivy sbom`) like any other.

It FAILS when a file in frontend/vendor/ is not in VENDORED or its banner no longer yields a
version: a library added or swapped without this list knowing would be a component nobody scans.

  python tools/ci/vendor_sbom.py OUT.cdx.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "frontend" / "vendor"

# file -> (npm package, regex finding the version in the file's first 64 KiB)
VENDORED = {
    "d3.min.js": ("d3", re.compile(r"https://d3js\.org v(\d+\.\d+\.\d+)")),
    "marked.min.js": ("marked", re.compile(r"marked v(\d+\.\d+\.\d+)")),
    "mermaid.min.js": ("mermaid", re.compile(r'\bversion:"(\d+\.\d+\.\d+)"')),
}


def components() -> tuple[list[dict], list[str]]:
    comps, errors = [], []
    present = sorted(p.name for p in VENDOR.iterdir() if p.is_file())
    for name in present:
        if name not in VENDORED:
            errors.append(f"frontend/vendor/{name} is not in tools/ci/vendor_sbom.py VENDORED")
            continue
        pkg, rx = VENDORED[name]
        data = (VENDOR / name).read_bytes()
        head = data[:65536].decode("utf-8", errors="replace")
        m = rx.search(head) or rx.search(data.decode("utf-8", errors="replace"))
        if not m:
            errors.append(f"frontend/vendor/{name}: no version banner matching {rx.pattern}")
            continue
        ver = m.group(1)
        comps.append({
            "type": "library", "bom-ref": f"pkg:npm/{pkg}@{ver}", "name": pkg, "version": ver,
            "purl": f"pkg:npm/{pkg}@{ver}",
            "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(data).hexdigest()}],
            "properties": [{"name": "atlas:path", "value": f"frontend/vendor/{name}"}],
        })
    return comps, errors


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    comps, errors = components()
    for e in errors:
        print(f"::error::{e}")
    if errors:
        return 1
    bom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": {"components": [{"type": "application", "name": "atlas tools/ci/vendor_sbom.py"}]},
            "component": {"type": "application", "name": "atlas-board-vendored-js",
                          "bom-ref": "atlas-board-vendored-js"},
        },
        "components": comps,
        "dependencies": [{"ref": "atlas-board-vendored-js", "dependsOn": [c["bom-ref"] for c in comps]}],
    }
    Path(sys.argv[1]).write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")
    for c in comps:
        print(f"{c['purl']}  ({c['properties'][0]['value']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
