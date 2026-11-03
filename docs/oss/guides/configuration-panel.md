---
audience: operations-security
---

# The configuration panel

Atlas describes its own parts as nodes: the system node `atlas-system` and one child per part
(`atlas-system-api`, `atlas-system-mcp`, `atlas-system-postgres`, `atlas-system-gateway`,
`atlas-system-search`, `atlas-system-retrieval`). On each of these nodes the board's node panel has a
**Configuration** tab for the instance owner. It lists every setting that part reads, its effective
value, and where that value comes from.

Showing is always on. **Changing** a setting from the board is a second phase that is off unless the
install turns it on with `ATLAS_CONFIG_WRITES=1`. Every setting itself is documented in
[Configuration](../CONFIG.md).

## Seeing the settings

**Who.** The instance owner in a browser session, or an agent token that holds the `admin` scope and
is linked to a live owner. Everyone else gets `403 forbidden`, and the board asks only for an owner,
so nobody else sees the tab or makes a request that fails. The configuration names hosts, origins,
identity-provider details and which secrets exist, which is operator knowledge.

**Which nodes.** A node gets the tab when the configuration response has a part with its id: the
system node and its six part nodes. A part with no setting of its own still has a section, with a
note saying why (the MCP service and the gateway read their own settings; search has none of its
own).

**What each row shows.** The data is `GET /api/config` (`Cache-Control: no-store`), one section per
part node:

- **A plain setting** shows its effective value and its source:

  | Source | Meaning |
  |---|---|
  | `env` | the variable is set in the API process's environment |
  | `derived` | unset, and computed from another variable that is set (for example the sign-in origins from `ATLAS_PUBLIC_URL`) |
  | `board` | set from the board (phase 2 only) |
  | `default` | unset; the code's default applies |

- **A secret** shows only its state: `set from file`, `set from env` or `not set`. Never a value, a
  prefix, a hash or a length. The state is decided without opening the file: a configured path that
  names a readable, non-empty file counts as set from file.
- **A bootstrap setting** is one the API needs before it can start: the database URL, the password
  pepper, the token pepper and the sign-in origins. A bootstrap setting that holds a credential is
  shown exactly like a secret.

The values are the running API process's own, read through the same code the API uses. A setting of
the retrieval add-on that is not read while the add-on is off says so instead of showing a value, and
the retrieval part carries the add-on's own status (off, on, or unavailable with the first problem
found). If a setting's current value does not parse, the row says so and shows no value.

## Changing settings from the board (opt-in)

**Off by default.** With `ATLAS_CONFIG_WRITES` unset or off, the write route is not registered at
all: `PUT /api/config/<name>` answers exactly like a path that never existed, the table that would
hold board values is never created or read, and the tab is read-only.

With `ATLAS_CONFIG_WRITES=1`, a row that can be changed gets a **Change** control:

- **Only the owner, in a browser session, with their password on every change.** No token can change
  configuration, whatever its scopes, an owner's `admin` token included (`403 session_required`). A
  member gets `403 forbidden`. A missing password is `403 step_up_required`, and a wrong one
  `403 password_incorrect`, recorded as a failed login (`login_fail`, reason `config_write:...`) that
  counts against the login rate limiter.
- **An owner who signs in only through single sign-on** has no Atlas password. With
  `ATLAS_OIDC_STEP_UP=on`, **Confirm with SSO** sends them through a fresh sign-in at the identity
  provider that names the setting, and the change is then saved without a password, once, within
  the step-up window. See
  [Configuration changes by SSO owners](identity-and-access.md#configuration-changes-by-sso-owners).
  Without step-up, such an owner changes configuration in the environment instead.
- **Only settings marked changeable.** Today these are `ATLAS_STRICT_API` (on or off) and
  `ATLAS_RETRIEVAL_TOP_K` (a whole number from 1 to 200). A setting is changeable only if it is a
  plain setting, its reader re-reads it on every use (so a board value really takes effect), and it is
  not a trust boundary. Every other setting is refused, each with a named reason:

  | Refusal | When |
  |---|---|
  | `404 unknown_setting` | no such setting |
  | `403 bootstrap_read_only` | a bootstrap setting, in every mode, forever |
  | `403 secret_not_changeable` | a secret |
  | `403 not_changeable` | any other setting not marked changeable, with the reason |
  | `409 environment_wins` | the environment sets it (see below) |
  | `400 bad_value` | the value does not validate |

- **Effect: at once.** Both changeable settings take effect immediately, and the response says
  `effect: "now"`. A value of `null` removes the board's value and returns the setting to its default.

### Precedence: the environment wins

1. **Environment.** A variable set in the API process's environment always wins. A board change to a
   setting the environment sets is refused with `409 environment_wins`, rather than stored and
   ignored. If the environment later starts setting a name the board had set, the environment wins
   from the next start; the board's value stays stored and unread until the variable is removed.
2. **Board.** A value set from the board is stored in the database (`config_value`) and applied
   again at every start, wherever the environment does not set that name.
3. **Default.** Neither sets it: the code's default.

Board values assume one API process, which is how the image runs it. With several workers, a change
would reach only the worker that served it until the others restart.

Turning the flag off later leaves the stored values in place and unread: every setting is then the
environment's or the default.

### What a change records

Every change is one `node.config.set` graph event on the setting's part node, by the person who made
it. Its detail names the setting, where its value came from before and after (for example `default`
to `board`) and when it takes effect. It **never holds the values**, old or new, because a node's
history is readable by more people than its configuration. Setting the same value again records
nothing. The value itself is in `config_value` and in `GET /api/config`.

A malformed setting in the environment stops the API at start-up, with a message that names the
variable.
