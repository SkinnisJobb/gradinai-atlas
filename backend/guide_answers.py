"""From the setup guide's recorded answers to the guardrails matrix and the CI/CD input
(STORY-atlas-first-cicd).

The guide (`setup_guide.py`, `questions.v1.json`) records answers under its own contract keys,
`<topic>.<question>`; `guardrails.matrix_from_answers` and `cicd.generate` read different
shapes. This module is the one place that translates, so neither side changes its contract.
Pure functions, except `latest_answers`, which reads `setup_answer` (and writes nothing).

THE MAPPING (guide key -> matrix_from_answers key):

    environments.names         -> environments (split as setup_guide.answers_list splits it)
    environments.production    -> production
    guardrails.approval_gates  has deploy_nonprod
                               -> human_approves: every non-production environment,
                                  agent_deploys: none, pipeline_deploys: none
    guardrails.agent_actions   has deploy_nonprod (and no such gate)
                               -> matrix_from_answers' defaults: the first non-production
                                  environment is an agent's, the one before production (with
                                  three or more) a person approves, the rest deploy through
                                  the pipeline
                               neither -> agent_deploys: none; the rest as the defaults
    cicd.gates                 -> required_checks (the generated pipeline names each gating
                                  job after its check, so branch protection can require it)
    (constant)                 -> name "From the setup guide"

    cicd.checks, cicd.gates, cicd.deploy_trigger, environments.promotion -> the CI/CD input.
    cicd.ci_platform           -> the platform, when it is one the generator knows.

WHAT THE MATRIX CANNOT SAY, reported in `notes` rather than dropped in silence: an agent
deploying to production (`guardrails.agent_actions` has deploy_prod, or `who_deploys` is
`agent_after_approval`/`pipeline_on_merge`) — the matrix refuses it, so production's deploy
stays a person's and the pipeline deploys only after that person approves; and agents merging
(`who_merges` other than human_only, or `merge` among the agent actions) — the matrix built
from answers keeps merging with a person. Edit the matrix by hand for anything else.
"""
from __future__ import annotations

from typing import Any, Optional

import setup_guide

TOPICS = ("environments", "guardrails", "cicd")
#: Every guide key this module reads, by topic; verify_cicd.py checks each is a question of
#: the shipped set, so a renamed question turns it red instead of being read as unanswered.
READS = {"environments": ("names", "production", "promotion"),
         "guardrails": ("agent_actions", "approval_gates", "who_merges", "who_deploys"),
         "cicd": ("ci_platform", "checks", "gates", "deploy_trigger")}
PLATFORM_OF = {"github_actions": "github", "gitlab_ci": "gitlab", "azure_pipelines": "azure"}
MATRIX_NAME = "From the setup guide"


class GuideAnswerError(ValueError):
    """The guide's answers are missing or do not fit. `problems` lists each."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("the setup guide's answers cannot be used:\n  - "
                         + "\n  - ".join(self.problems))


def latest_answers(s) -> dict[str, dict]:
    """{topic id: its newest recorded answers} from `setup_answer`. Reads only."""
    from sqlalchemy import select
    from models_setup import SetupAnswer
    out: dict[str, dict] = {}
    for r in s.scalars(select(SetupAnswer).order_by(SetupAnswer.id)).all():
        out[r.topic_id] = dict(r.answers or {})
    return out


def _topic(guide: dict, tid: str, problems: list[str]) -> dict:
    got = guide.get(tid)
    if not isinstance(got, dict):
        problems.append(f"topic {tid!r} is not answered (run the setup guide's {tid} topic)")
        return {}
    return got


def matrix_answers(guide: dict) -> tuple[dict, list[str]]:
    """(the input for guardrails.matrix_from_answers, notes on what it cannot say)."""
    problems: list[str] = []
    env = _topic(guide, "environments", problems)
    gr = _topic(guide, "guardrails", problems)
    cicd = guide.get("cicd") or {}
    if problems:
        raise GuideAnswerError(problems)
    names = setup_guide.answers_list(env.get("names", ""))
    production = (env.get("production") or "").strip()
    if not names:
        problems.append("environments.names: no environment named")
    if production not in names:
        problems.append(f"environments.production: {production!r} is not one of "
                        f"environments.names ({', '.join(names)})")
    if problems:
        raise GuideAnswerError(problems)
    nonprod = [n for n in names if n != production]
    agent_actions = set(gr.get("agent_actions") or [])
    gates = set(gr.get("approval_gates") or [])
    out: dict[str, Any] = {"environments": names, "production": production, "name": MATRIX_NAME}
    if "deploy_nonprod" in gates:
        out.update(agent_deploys=[], human_approves=nonprod, pipeline_deploys=[])
    elif "deploy_nonprod" not in agent_actions:
        out["agent_deploys"] = []
    if cicd.get("gates"):
        out["required_checks"] = list(cicd["gates"])
    notes = []
    if "deploy_prod" in agent_actions or gr.get("who_deploys") in ("agent_after_approval",
                                                                   "pipeline_on_merge"):
        notes.append("guardrails: agents never hold deploy rights in production, so the "
                     "production deploy stays a person's; the pipeline deploys it only after a "
                     "person approves (the answer is kept in its ADR, not in the matrix)")
    if "merge" in agent_actions or gr.get("who_merges", "human_only") != "human_only":
        notes.append("guardrails: a matrix built from answers keeps merging with a person; "
                     "edit the matrix by hand if agents should merge somewhere")
    return out, notes


def cicd_input(guide: dict) -> dict:
    """The generator's CI/CD input from the guide's `cicd` (and `environments.promotion`)."""
    problems: list[str] = []
    c = _topic(guide, "cicd", problems)
    if problems:
        raise GuideAnswerError(problems)
    out: dict[str, Any] = {"checks": list(c.get("checks") or []),
                           "gates": list(c.get("gates") or [])}
    if c.get("deploy_trigger"):
        out["deploy_trigger"] = c["deploy_trigger"]
    promotion = (guide.get("environments") or {}).get("promotion")
    if promotion:
        out["promotion"] = promotion
    return out


def platform_of(guide: dict) -> Optional[str]:
    """The generator's platform for `cicd.ci_platform`, or None (jenkins, other, none)."""
    return PLATFORM_OF.get((guide.get("cicd") or {}).get("ci_platform", ""))


def from_guide(guide: dict) -> tuple[dict, dict, list[str]]:
    """(validated matrix, CI/CD input, notes). Raises GuideAnswerError or MatrixError."""
    import guardrails
    answers, notes = matrix_answers(guide)
    matrix = guardrails.matrix_from_answers(answers)
    return matrix, cicd_input(guide), notes


def load_answers_file(path: str) -> dict:
    """An answers file for `cicd generate --answers`, as guide-shaped {topic: answers}.

    Accepted shapes: {"cicd": {...}, "environments": {"promotion": ...}} (the guide's own
    topics), or the cicd topic's answers alone ({"checks", "gates", "deploy_trigger",
    "ci_platform"?, "promotion"?}). The cicd answers are checked against the guide's own
    question set (`setup_guide.normalise_answers`), so a typo is named, not ignored."""
    import guardrails
    with open(path, encoding="utf-8") as fh:
        raw = guardrails.parse_text(fh.read(), path)
    if not isinstance(raw, dict):
        raise GuideAnswerError([f"{path}: must be a mapping"])
    if "cicd" in raw:
        guide = {k: v for k, v in raw.items()}
    else:
        flat = dict(raw)
        promotion = flat.pop("promotion", None)
        guide = {"cicd": flat}
        if promotion is not None:
            guide["environments"] = {"promotion": promotion}
    cicd = dict(guide.get("cicd") or {})
    cicd.setdefault("ci_platform", "other")  # the generator's --platform decides, not this key
    topic = next(t for t in setup_guide.load_question_set()["topics"] if t["id"] == "cicd")
    try:
        guide["cicd"] = setup_guide.normalise_answers(topic, cicd)
    except setup_guide.AnswerError as exc:
        raise GuideAnswerError([f"{path}: cicd.{exc.field.split('.', 1)[-1]}: {exc.detail}"]) from None
    return guide


__all__ = ["TOPICS", "READS", "GuideAnswerError", "latest_answers", "matrix_answers",
           "cicd_input", "platform_of", "from_guide", "load_answers_file"]
