"""
TASK-1120 — map the free-text `node.owner` string onto real accounts, for
WORKITEMS ONLY.

`owner` is a `String(64)` that reads "Alex" on 359 of 374 workitems and joins
to nothing: the account it names has subject `alex`, so no query could ever
connect the two. TASK-1118 added `node.assignee_user_id`, which is an
`auth_user.id`. This script moves the corpus across.

WHAT IT DOES NOT TOUCH, and this is the criterion most worth checking:
**the ~34 OBJECT-family nodes that also carry an `owner` string.** There the
field means something different (who looks after this part of the world) and
is not being replaced. Every statement below is scoped to
`node_type.family = 'workitem'`, and the report prints the object-family count
and checksum before and after so a scope slip is visible rather than silent.

THE MAPPING is derived, never hardcoded: each distinct `owner` value is matched
case-insensitively against `auth_user.subject` and `auth_user.name`. Anything
that does not match exactly one active human account is REPORTED AND SKIPPED —
it is not guessed at, and it is not defaulted to the owner. On the corpus as
measured 2026-08-17 that leaves the three agent-owned rows (`atlas-agent` x2,
`company-agent` x1) for a human to decide, which is the whole judgement call in
this task and is deliberately not automated: an agent is not an assignable
account (ADR 0034), so there is no right answer for a script to reach.

`owner` IS LEFT IN PLACE on the rows it migrates. Two reasons, and neither is
timidity: `_reconcile_graph_schema` cannot drop a column anyway (its whole DDL
vocabulary is ADD COLUMN), and leaving the old value readable makes this
script's own output checkable after the fact. Nothing should WRITE it for a
workitem again — that is the code change in TASK-1118, not a data change here.

DRY RUN BY DEFAULT. It prints the full mapping and rolls back. `--apply`
commits. Per the classifier boundary and AGENTS.md §4, an agent builds and
dry-runs this; the owner runs the committing pass.

    python migrate_assignee_from_owner.py              # dry run, rolls back
    python migrate_assignee_from_owner.py --apply      # commits

WHEN DERIVATION IS NOT ENOUGH, SAY SO ON THE COMMAND LINE. On live the owner's
account is `alex-admin` and carries NO display name, so the string "Alex" matches
neither its `subject` nor its `name` and all 372 of its workitems are skipped —
correctly, because the script must not guess which account a name belongs to.
The answer is an EXPLICIT mapping, not a lookup table baked into this file:

    python migrate_assignee_from_owner.py --map "Alex=alex-admin"
    python migrate_assignee_from_owner.py --map "Alex=alex-admin" --apply

`--map` is repeatable. It takes `OLD_OWNER_STRING=username`, and the username
must still resolve to exactly one active human account — an unknown or
ambiguous one is refused, not applied. Putting the decision in the invocation
means it appears in the transcript beside its result, which a constant in the
source would not.

Against live, from the repo root on the server:

    docker exec atlas-api python migrate_assignee_from_owner.py
    docker exec atlas-api python migrate_assignee_from_owner.py --apply
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import func, select  # noqa: E402

import app  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import Node, NodeType  # noqa: E402

#: How many ids to print per skipped `owner` value before summarising.
LIST_CAP = 20


def _parse_map(argv: list[str]) -> dict[str, str]:
    """`--map "Alex=alex-admin"` (repeatable) -> {owner_string_lower: username_lower}."""
    out: dict[str, str] = {}
    for i, a in enumerate(argv):
        if a == "--map" and i + 1 < len(argv):
            raw = argv[i + 1]
            if "=" not in raw:
                raise SystemExit(f"--map wants OLD=username, got {raw!r}")
            old, new = raw.split("=", 1)
            out[old.strip().lower()] = new.strip().lower()
    return out


def _object_owner_fingerprint(s) -> tuple[int, str]:
    """`(count, checksum)` over every OBJECT-family node carrying an `owner`.

    Printed before and after. If either number moves, the migration reached
    outside the workitem family and the run must be rolled back — that is the
    single most valuable line of output this script produces, because a scope
    slip changes rows nobody is looking at.
    """
    rows = s.execute(
        select(Node.id, Node.owner)
        .join(NodeType, Node.type == NodeType.id)
        .where(NodeType.family == "object", Node.owner.is_not(None))
        .order_by(Node.id)
    ).all()
    digest = hashlib.sha256(
        "\n".join(f"{nid}={owner}" for nid, owner in rows).encode("utf-8")
    ).hexdigest()[:16]
    return len(rows), digest


def main(apply: bool, overrides: dict[str, str] | None = None) -> int:
    overrides = overrides or {}
    with app.session() as s:
        # -- who is assignable ------------------------------------------------
        people = s.scalars(
            select(AuthUser)
            .where(AuthUser.is_service.is_(False))
            .where(AuthUser.disabled_at.is_(None))
        ).all()
        by_key: dict[str, list[AuthUser]] = {}
        for u in people:
            for key in filter(None, (u.subject, u.name)):
                by_key.setdefault(key.strip().lower(), []).append(u)

        print("accounts available as assignees:")
        for u in sorted(people, key=lambda x: x.subject):
            print(f"  {u.subject:<12} {u.name or '(no display name)':<18} {u.id}")

        by_subject = {u.subject.strip().lower(): u for u in people}
        if overrides:
            print("")
            print("explicit --map overrides:")
            for old, sub in overrides.items():
                target = by_subject.get(sub)
                if target is None:
                    raise SystemExit(
                        f"--map {old}={sub}: no active human account with that username. "
                        f"Known: {sorted(by_subject)}"
                    )
                print(f"  {old!r} -> {target.subject} ({target.id})")

        # -- the object-family fingerprint, BEFORE ----------------------------
        before_count, before_digest = _object_owner_fingerprint(s)
        print(f"\nobject-family nodes carrying an owner (MUST NOT MOVE): "
              f"{before_count} rows, checksum {before_digest}")

        # -- the workitem corpus ---------------------------------------------
        rows = s.execute(
            select(Node.owner, func.count())
            .join(NodeType, Node.type == NodeType.id)
            .where(NodeType.family == "workitem")
            .group_by(Node.owner)
            .order_by(func.count().desc())
        ).all()

        print("\nworkitem `owner` values:")
        planned: list[tuple[str, str]] = []   # (owner value, user id)
        skipped: list[tuple[str, int, str]] = []
        for owner, n in rows:
            if owner is None:
                print(f"  {'(null)':<16} {n:>4}  -> stays unassigned")
                continue
            key = owner.strip().lower()
            if key in overrides:
                matches = [by_subject[overrides[key]]]
            else:
                matches = by_key.get(key, [])
            uniq = {u.id: u for u in matches}
            if len(uniq) == 1:
                uid = next(iter(uniq))
                via = " (--map)" if owner.strip().lower() in overrides else ""
                print(f"  {owner:<16} {n:>4}  -> {uniq[uid].subject} ({uid}){via}")
                planned.append((owner, uid))
            else:
                why = "no matching account" if not uniq else f"ambiguous: {sorted(uniq)}"
                print(f"  {owner:<16} {n:>4}  -> SKIPPED, {why}")
                skipped.append((owner, n, why))

        # -- apply -------------------------------------------------------------
        moved = 0
        for owner, uid in planned:
            result = s.execute(
                Node.__table__.update()
                .where(Node.id.in_(
                    select(Node.id)
                    .join(NodeType, Node.type == NodeType.id)
                    .where(NodeType.family == "workitem", Node.owner == owner)
                ))
                .values(assignee_user_id=uid)
            )
            moved += result.rowcount or 0

        s.flush()
        after_count, after_digest = _object_owner_fingerprint(s)
        assigned = s.scalar(
            select(func.count()).select_from(Node).where(Node.assignee_user_id.is_not(None)))

        print(f"\nworkitems given an assignee: {moved}")
        print(f"workitems now carrying one:  {assigned}")
        print(f"object-family fingerprint after: {after_count} rows, checksum {after_digest}")

        scope_ok = (before_count, before_digest) == (after_count, after_digest)
        print("object-family untouched: " + ("YES" if scope_ok else "*** NO — ROLLING BACK ***"))

        if skipped:
            print("\nLEFT FOR A HUMAN (not guessed at):")
            for owner, n, why in skipped:
                print(f"  {owner:<16} {n:>4} workitem(s) — {why}")
                ids = s.scalars(
                    select(Node.id)
                    .join(NodeType, Node.type == NodeType.id)
                    .where(NodeType.family == "workitem", Node.owner == owner)
                    .order_by(Node.id)
                ).all()
                # CAPPED, and it says so. The expected skipped set is the three
                # agent-owned rows; a run that skips hundreds means the account
                # lookup found nothing, and printing 359 ids on one line buries
                # that fact under the evidence for it.
                shown = ids[:LIST_CAP]
                more = len(ids) - len(shown)
                print(f"      {', '.join(shown)}"
                      + (f"  ... and {more} more" if more else ""))

        if not scope_ok:
            s.rollback()
            print("\nROLLED BACK — the object family moved, which this script must never do.")
            return 1

        if apply:
            s.commit()
            print("\nCOMMITTED.")
        else:
            s.rollback()
            print("\nDRY RUN — rolled back. Re-run with --apply to commit.")
        return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv, overrides=_parse_map(sys.argv)))
