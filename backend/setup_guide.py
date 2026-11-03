"""The first-run setup guide's shared core (STORY-atlas-setup-guide-adrs): the question set.

The guide interviews whoever installs Atlas (guardrails, CI/CD, environments, identity, secrets,
the work hierarchy) and records each answered topic as an Atlas-native ADR (ADR 0045, source
`atlas`), Proposed until a person accepts it. This module is the part every client shares: the
versioned question set as DATA, its validation, answer validation, and the rendering of an answer
into the ADR's markdown. The routes are `routers/setup.py`, the state table `models_setup.py`.
The first client is the agent interview (the MCP tool `atlas_setup_guide` and the client pack's
`setup-guide` skill); a board wizard is a later client of the same API.

THE QUESTION SET IS DATA. The shipped set is `data/setup_guide/questions.v1.json`. An install
adds its own topics, with no code change, by naming a second file of the same shape in
`ATLAS_SETUP_QUESTIONS_FILE`; its topics are asked after the shipped ones, and a topic id that
repeats one already loaded is an error, so an install can add but never silently redefine.
Both files are read and validated every time the guide is used (they are a few KB), never at
boot: boot stays a read. A bad file is a `QuestionSetError` whose message names the file and
the place in it (`topics[2].questions[0].type: ...`); the routes answer it as a 503.

SHAPE (both files)::

    {"version": "<string>", "topics": [
      {"id": "<slug>", "title": "...", "summary": "...",
       "questions": [{"id": "<slug>", "type": "choice|multi|text", "prompt": "...",
                      "options": [{"value": "<slug>", "label": "..."}],   # choice and multi
                      "required": true}],                                  # default true
       "adr": {"title": "...", "context": "...", "decision": "...", "consequences": "..."}}]}

`adr.*` are MADR sections. `{question_id}` in them is replaced by the answer as a person reads
it (option labels, comma-joined; text as given; "none" for an empty multi, "not stated" for an
unanswered optional question). The ADR's content ends with an `## Answers` table of the raw
keys, so a reader (and a later tool) can get the exact values back from the record itself.

THE ANSWER KEYS ARE A CONTRACT. Later stories read answers by `<topic>.<question>` and the
option VALUES, never the labels or prompts (which may be reworded): the guardrail matrix
(STORY-atlas-guardrails-per-env) and the first CI/CD flow (STORY-atlas-first-cicd). The shipped
keys, version 1 (`verify_setup_guide.py` pins this list, so a rename turns it red):

    guardrails.agent_actions     multi   read file_work write_docs open_pr merge deploy_nonprod
                                         deploy_prod change_config
    guardrails.approval_gates    multi   merge deploy_nonprod deploy_prod schema_change
                                         acceptance_change secret_change access_change
    guardrails.who_merges        choice  human_only agent_after_approval agent_when_green
    guardrails.who_deploys       choice  human_only agent_after_approval pipeline_on_merge
    guardrails.notes             text    optional
    cicd.ci_platform             choice  github_actions gitlab_ci jenkins azure_pipelines other none
    cicd.checks                  multi   build unit_tests integration_tests lint type_check sast
                                         dependency_scan secret_scan
    cicd.gates                   multi   the same values as cicd.checks
    cicd.deploy_trigger          choice  manual on_merge on_tag scheduled
    environments.names           text    comma-separated, least to most protected
    environments.production      text    one of the names
    environments.promotion       choice  sequential independent
    identity.login               choice  local oidc both
    identity.rbac                choice  off projects
    identity.default_visibility  choice  shared private
    secrets.storage              choice  files vault cloud_secret_manager ci_secrets other
    secrets.agent_access         choice  never path_only scoped
    secrets.rotation             choice  manual scheduled on_incident
    work_hierarchy.levels        choice  epic_story_task story_task task_only
    work_hierarchy.who_files     multi   people agents
    work_hierarchy.acceptance    choice  people agent_proposes

`answers_list()` splits a comma-separated text answer (`environments.names`) the same way for
every reader. A new meaning gets a new id, or a `questions.v2.json`, never a reused one.

Run `python setup_guide.py --check FILE` to validate an extra question file before setting it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

import settings

SHIPPED_FILE = Path(__file__).resolve().parent / "data" / "setup_guide" / "questions.v1.json"

QUESTION_TYPES = ("choice", "multi", "text")
ADR_SECTIONS = ("title", "context", "decision", "consequences")
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
#: Longest accepted text answer, so one answer cannot become a megabyte ADR.
TEXT_MAX = 2000
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_-]*)\}")


class QuestionSetError(ValueError):
    """A question file is unreadable or malformed. The message names the file and the place."""


class AnswerError(ValueError):
    """An answer does not fit its topic. `field` names the offending key, for a 400."""

    def __init__(self, field: str, error: str, detail: str):
        super().__init__(detail)
        self.field, self.error, self.detail = field, error, detail


def _read(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise QuestionSetError(f"{path}: cannot be read ({e.strerror or e})") from None
    try:
        return json.loads(text)
    except ValueError as e:
        raise QuestionSetError(f"{path}: not valid JSON ({e})") from None


def _validate(doc: Any, where: str, taken: set) -> tuple[str, list[dict]]:
    """(version, topics) of one file, or `QuestionSetError` naming `where` and the place."""
    def bad(at: str, msg: str):
        raise QuestionSetError(f"{where}: {at}: {msg}")

    if not isinstance(doc, dict):
        bad("top level", "must be an object with `version` and `topics`")
    version = doc.get("version")
    if not isinstance(version, str) or not version.strip():
        bad("version", "must be a non-empty string")
    topics = doc.get("topics")
    if not isinstance(topics, list) or not topics:
        bad("topics", "must be a non-empty list")
    out = []
    for i, t in enumerate(topics):
        at = f"topics[{i}]"
        if not isinstance(t, dict):
            bad(at, "must be an object")
        tid = t.get("id")
        if not isinstance(tid, str) or not _SLUG.match(tid):
            bad(f"{at}.id", f"{tid!r} is not a lower-case slug (a-z, 0-9, _ and -)")
        if tid in taken:
            bad(f"{at}.id", f"topic {tid!r} is already defined; an added file may not redefine one")
        taken.add(tid)
        for f in ("title",):
            if not isinstance(t.get(f), str) or not t[f].strip():
                bad(f"{at}.{f}", "must be a non-empty string")
        if "summary" in t and not isinstance(t["summary"], str):
            bad(f"{at}.summary", "must be a string")
        qs = t.get("questions")
        if not isinstance(qs, list) or not qs:
            bad(f"{at}.questions", "must be a non-empty list")
        qids = set()
        questions = []
        for j, q in enumerate(qs):
            qat = f"{at}.questions[{j}]"
            if not isinstance(q, dict):
                bad(qat, "must be an object")
            qid = q.get("id")
            if not isinstance(qid, str) or not _SLUG.match(qid):
                bad(f"{qat}.id", f"{qid!r} is not a lower-case slug")
            if qid in qids:
                bad(f"{qat}.id", f"question {qid!r} appears twice in topic {tid!r}")
            qids.add(qid)
            qtype = q.get("type")
            if qtype not in QUESTION_TYPES:
                bad(f"{qat}.type", f"{qtype!r} is not one of {', '.join(QUESTION_TYPES)}")
            if not isinstance(q.get("prompt"), str) or not q["prompt"].strip():
                bad(f"{qat}.prompt", "must be a non-empty string")
            required = q.get("required", True)
            if not isinstance(required, bool):
                bad(f"{qat}.required", "must be true or false")
            options = q.get("options")
            if qtype == "text":
                if options is not None:
                    bad(f"{qat}.options", "a text question has no options")
                options = []
            else:
                if not isinstance(options, list) or not options:
                    bad(f"{qat}.options", f"a {qtype} question needs a non-empty list of options")
                seen = set()
                for k, o in enumerate(options):
                    oat = f"{qat}.options[{k}]"
                    if not isinstance(o, dict) or not isinstance(o.get("value"), str) \
                            or not _SLUG.match(o["value"]):
                        bad(oat, "must be {\"value\": <lower-case slug>, \"label\": <text>}")
                    if not isinstance(o.get("label"), str) or not o["label"].strip():
                        bad(f"{oat}.label", "must be a non-empty string")
                    if o["value"] in seen:
                        bad(f"{oat}.value", f"{o['value']!r} appears twice")
                    seen.add(o["value"])
            questions.append({"id": qid, "type": qtype, "prompt": q["prompt"],
                              "required": required,
                              "options": [{"value": o["value"], "label": o["label"]}
                                          for o in options]})
        adr = t.get("adr")
        if not isinstance(adr, dict):
            bad(f"{at}.adr", "must be an object with " + ", ".join(ADR_SECTIONS))
        for f in ADR_SECTIONS:
            if not isinstance(adr.get(f), str) or not adr[f].strip():
                bad(f"{at}.adr.{f}", "must be a non-empty string")
            unknown = sorted(set(_PLACEHOLDER.findall(adr[f])) - qids)
            if unknown:
                bad(f"{at}.adr.{f}", f"names {', '.join(unknown)}, which is not a question of "
                                     f"topic {tid!r}")
        out.append({"id": tid, "title": t["title"], "summary": t.get("summary", ""),
                    "questions": questions,
                    "adr": {f: adr[f] for f in ADR_SECTIONS}})
    return version.strip(), out


def load_question_set(environ: Optional[Mapping[str, str]] = None) -> dict:
    """The question set in force: the shipped topics, then the extra file's (if one is set).

    {"version": "1" or "1+<extra version>", "shipped_version", "extra": None or
    {"file", "version"}, "topics": [... each with "source": "shipped"|"extra"]}.
    Raises `QuestionSetError` naming the file and the place."""
    taken: set = set()
    version, topics = _validate(_read(SHIPPED_FILE), SHIPPED_FILE.name, taken)
    for t in topics:
        t["source"] = "shipped"
    out = {"version": version, "shipped_version": version, "extra": None, "topics": topics}
    extra = settings.load_setup_questions_file(environ)
    if extra:
        path = Path(extra)
        ev, et = _validate(_read(path), f"{settings.SETUP_QUESTIONS_FILE_ENV} ({path})", taken)
        for t in et:
            t["source"] = "extra"
        out["topics"] = topics + et
        out["extra"] = {"file": path.name, "version": ev}
        out["version"] = f"{version}+{ev}"
    return out


def normalise_answers(topic: dict, answers: Any) -> dict:
    """The answers to one topic, checked and in canonical form, or `AnswerError` naming the key.

    choice: one option value. multi: a list of option values, de-duplicated and in the options'
    own order (an empty list is an answer: "none of these"). text: a stripped string, at most
    TEXT_MAX characters. A required question must be answered; an optional one may be left
    out (it is then absent, never stored as empty). An unknown key is refused, not dropped."""
    if not isinstance(answers, dict):
        raise AnswerError("answers", "bad_answers", "answers is an object of question id -> answer")
    by_id = {q["id"]: q for q in topic["questions"]}
    unknown = sorted(set(answers) - set(by_id))
    if unknown:
        raise AnswerError(f"answers.{unknown[0]}", "unknown_question",
                          f"topic {topic['id']!r} has no question {unknown[0]!r}; its questions "
                          f"are {', '.join(by_id)}")
    out: dict = {}
    for q in topic["questions"]:
        field = f"answers.{q['id']}"
        v = answers.get(q["id"])
        if v is None or (q["type"] == "text" and isinstance(v, str) and not v.strip()):
            if q["required"]:
                raise AnswerError(field, "missing_answer", f"{q['id']!r} needs an answer: {q['prompt']}")
            continue
        values = [o["value"] for o in q["options"]]
        if q["type"] == "choice":
            if not isinstance(v, str) or v not in values:
                raise AnswerError(field, "bad_choice",
                                  f"{v!r} is not an option of {q['id']!r}; legal: {', '.join(values)}")
            out[q["id"]] = v
        elif q["type"] == "multi":
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise AnswerError(field, "bad_multi", f"{q['id']!r} takes a list of option values")
            wrong = [x for x in v if x not in values]
            if wrong:
                raise AnswerError(field, "bad_choice",
                                  f"{wrong[0]!r} is not an option of {q['id']!r}; legal: {', '.join(values)}")
            out[q["id"]] = [x for x in values if x in v]
        else:
            if not isinstance(v, str):
                raise AnswerError(field, "bad_text", f"{q['id']!r} takes text")
            v = v.strip()
            if len(v) > TEXT_MAX:
                raise AnswerError(field, "text_too_long", f"{q['id']!r} is at most {TEXT_MAX} characters")
            out[q["id"]] = v
    return out


def answers_list(text: str) -> list[str]:
    """A comma-separated text answer as a list: stripped, empty items dropped, order kept."""
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def _readable(q: dict, answers: dict) -> str:
    if q["id"] not in answers:
        return "not stated"
    v = answers[q["id"]]
    labels = {o["value"]: o["label"] for o in q["options"]}
    if q["type"] == "choice":
        return labels[v]
    if q["type"] == "multi":
        return ", ".join(labels[x] for x in v) if v else "none"
    return v


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_adr(topic: dict, answers: dict, *, version: str,
               supersedes: Optional[str] = None) -> tuple[str, str]:
    """(title, markdown) of the ADR one topic's answers make. MADR sections, then the raw keys."""
    readable = {q["id"]: _readable(q, answers) for q in topic["questions"]}

    def fill(s: str) -> str:
        return _PLACEHOLDER.sub(lambda m: readable.get(m.group(1), m.group(0)), s)

    adr = topic["adr"]
    title = fill(adr["title"]).strip()
    lines = [f"# {title}", "",
             f"- Recorded by: the setup guide (question set {version}, topic `{topic['id']}`)"]
    if supersedes:
        lines.append(f"- Supersedes: {supersedes}")
    lines += ["", "## Context and problem statement", "", fill(adr["context"]).strip(),
              "", "## Decision", "", fill(adr["decision"]).strip(),
              "", "## Consequences", "", fill(adr["consequences"]).strip(),
              "", "## Answers", "", "| Key | Answer |", "|---|---|"]
    for q in topic["questions"]:
        if q["id"] not in answers:
            continue
        v = answers[q["id"]]
        shown = ", ".join(f"`{x}`" for x in v) if isinstance(v, list) else (
            f"`{v}`" if q["type"] == "choice" else _cell(v))
        lines.append(f"| `{topic['id']}.{q['id']}` | {shown or 'none'} |")
    return title, "\n".join(lines) + "\n"


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--check":
        print("usage: python setup_guide.py --check FILE", file=sys.stderr)
        return 2
    try:
        load_question_set({settings.SETUP_QUESTIONS_FILE_ENV: argv[1]})
    except QuestionSetError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
