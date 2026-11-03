"""
The distribution seam (STORY-oss-23, phase A item A1), NEUTRAL COPY: every
estate-valued default atlas-api has, in one file and nowhere else.

ADDITIVE, OPT-IN, like every block in `settings.py`. "Estate-valued" means a default
that describes one particular install rather than the product: which seed profile
an empty database gets, the privacy page's mailbox and hosting paragraph, the host
and commands a stale-deploy warning names, whether the v0 tables exist, and (A4b)
the privacy page's controller line and company note, the v0 `Component.owner`
default and the words a password may not contain.
`settings.py` reads each of them from `backend/distribution.py` and keeps its own
names for them (`settings.DEFAULT_SEED_PROFILE` and the rest); an environment
variable still overrides each one.

THIS COPY IS THE PUBLIC ONE. In project-atlas it lives at
`tools/carve/overlay/backend/distribution.py` and nothing imports it from there;
the carve-out manifest overlays it onto `backend/distribution.py` in the exported
tree, replacing the homelab copy whole. Its values describe no install:

  * seed `none`: an empty database gets the install's own system node set and no
    work (`main.startup`, `system_node.seed`). `homelab` is not offered at all,
    because the export does not ship `seed.py`;
  * the v0 tables off, as `deploy/oss/.env.example` already sets them;
  * the privacy page names a placeholder mailbox and says, in plain words, that the
    operator has not described the hosting yet: an install sets
    ATLAS_PRIVACY_CONTACT and ATLAS_PRIVACY_HOSTING (the OSS .env.example does);
  * the deploy hints name `atlas.example.test` and carry no default command: each
    unset ATLAS_DEPLOY_HINT_* answers "(no command configured: set <variable>)".
    Host and commands equal the MCP's neutral copy, which verify_deploy_status.py checks.

`backend/verify_fresh_install.py` §G boots the app with this copy in front of the
homelab one and proves `none` is then the default, and that both copies define the
same names. A name added to one copy goes in the other in the same change.
Nothing here may import anything: `settings.py` is standard library only, and this
file is read at its import.
"""
from __future__ import annotations

#: Which copy this is. `neutral` here, `homelab` in project-atlas's own copy. Read
#: only by the verify scripts, to decide which pins apply; the product never branches on it.
NAME = "neutral"

#: STORY-oss-07: the seed profiles `ATLAS_SEED` accepts. `demo` is `demo_seed.py`
#: (one neutral project, one epic, two stories); `none` seeds no data beyond the
#: system node set; `self` is `self_seed.py` (Atlas's own world model), opt-in only.
SEED_PROFILES = ("demo", "none", "self")
#: What an unset `ATLAS_SEED` means.
DEFAULT_SEED_PROFILE = "none"

#: What an unset `ATLAS_LEGACY_V0` means: no v0 Component/Gate/Requirement/Decision
#: tables, and their four routes answer 404. `ATLAS_LEGACY_V0=1` turns them on.
DEFAULT_LEGACY_V0 = False

#: The privacy page's mailbox. A placeholder on a reserved domain: every install
#: sets ATLAS_PRIVACY_CONTACT to a mailbox someone reads.
DEFAULT_PRIVACY_CONTACT = "privacy@example.com"
#: The privacy page's "Where it lives" paragraph, plain text. It claims nothing about
#: an install it cannot know; the install describes its own with ATLAS_PRIVACY_HOSTING.
DEFAULT_PRIVACY_HOSTING = (
    "One PostgreSQL database run by the operator of this install. The operator has not "
    "yet described where that database is hosted; ask the privacy contact on this page."
)

#: The host the deploy hint sentences name, and `{host}` in any command. The same value as
#: the MCP's neutral copy (tools/carve/overlay/mcp/app/distribution.py):
#: backend/verify_deploy_status.py compares the two settings modules.
DEFAULT_DEPLOY_HINT_HOST = "atlas.example.test"
#: No default commands: how an install deploys is its own. For each ATLAS_DEPLOY_HINT_*
#: variable left unset, `settings.load_deploy_hints` answers with `NO_DEPLOY_HINT`, a
#: sentence naming the variable to set; nothing fails. Equal to the MCP's copy, as above.
DEFAULT_DEPLOY_HINTS = {}

#: The privacy page's controller line: who holds the data. A role, not a name; an
#: install that wants its own name there edits this copy.
DEFAULT_PRIVACY_CONTROLLER = "The operator of this install"
#: The privacy page's sentence after the controller line, HTML. Empty: the neutral
#: page has no company note, and the paragraph renders without it.
PRIVACY_COMPANY_NOTE = ""

#: What an unset `Component.owner` means on the v0 table (off by default here).
DEFAULT_COMPONENT_OWNER = "owner"

#: The context words a password may not contain: the product's name.
PASSWORD_CONTEXT_WORDS = ("atlas",)
