# Atlas for Claude Code

Connect [Claude Code](https://code.claude.com/docs) to your Atlas install with **your own** agent
token, and give it three skills for working with Atlas well.

| File | What it is |
|---|---|
| `mcp.template.json` | MCP server entry for Linux and macOS (helper run through `sh`) |
| `mcp.template.windows.json` | The same for Windows (helper run through PowerShell) |
| `helpers/atlas-mcp-headers.sh`, `helpers/atlas-mcp-headers.ps1` | The `headersHelper`: reads your token from a file and prints the `Authorization` header |
| `plugin/` | The `atlas` plugin: the `atlas-retrieval`, `file-task` and `setup-guide` skills |
| `.claude-plugin/marketplace.json` | Lets Claude Code install the plugin from this directory |
| `tests/` | What CI runs against this pack (a stub MCP endpoint; no Atlas needed) |

**The token is never written into a JSON file, a command line or an environment variable.** It
lives in one file readable only by you. Claude Code runs the helper on every connection
(`headersHelper`), and the helper reads the file and prints `{"Authorization":"Bearer ..."}`.
Claude Code also strips credential-looking environment variables (`*TOKEN*`, `*KEY*`, ...)
before it runs a helper from a project `.mcp.json`, so a file is the mechanism that works.

You need: Claude Code, your install's public URL (`ATLAS_PUBLIC_URL` in its `.env`, below
`https://atlas.example.com`), an Atlas account, and - if the install uses its own certificate
authority - the install's CA certificate.

## Three steps

### 1. Get a token into `~/.config/atlas/token`

An operator mints an agent token for you on the machine that runs the stack, from the
install's `deploy/oss` directory. The token is written only to the `--out` file, never printed:

```sh
docker compose exec atlas-api \
  atlas-admin token mint --user <you> --purpose claude-code --scopes read,write --out /tmp/claude-code.token
```

- `--user` is your Atlas username (or `<idp>:<subject>`, or your email for an SSO account that
  has signed in once). The agent is named `<you>-claude-code` and is linked to you, so
  everything it writes is attributed to it and accountable to you.
- `--scopes` defaults to `read`; filing work (the `file-task` skill) needs `write`. Scopes are
  capped by your role. The token expires (`--days`, at most 365).
- Signed in to the board with a password? You can mint your own instead, under
  *Settings -> Agent credentials*.

Move the file to the machine you run Claude Code on without printing it, then delete the
copy in the container. On the same machine:

```sh
mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas
( umask 077; docker compose exec -T atlas-api cat /tmp/claude-code.token > ~/.config/atlas/token )
docker compose exec -T atlas-api rm /tmp/claude-code.token
```

From another machine, run the same `cat` over ssh with the redirect on your side
(`( umask 077; ssh <stack-host> 'cd <install>/deploy/oss && docker compose exec -T atlas-api cat /tmp/claude-code.token' > ~/.config/atlas/token )`).

Then put the helper next to it:

```sh
# Linux, macOS
cp helpers/atlas-mcp-headers.sh ~/.config/atlas/
chmod 600 ~/.config/atlas/token    # umask applies to a new file only; this covers an existing one
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$HOME\.config\atlas" | Out-Null
Copy-Item helpers\atlas-mcp-headers.ps1 "$HOME\.config\atlas\"
# Only you may read the token: drop inherited access, grant yourself full control.
icacls "$HOME\.config\atlas\token" /inheritance:r /grant:r "$($env:USERNAME):(F)"
```

The helper refuses a token file that group or others can read (Windows: Everyone, Users or
Authenticated Users), and anything on its first line that is not a token.

Check it (prints the header shape, not the token):

```sh
sh ~/.config/atlas/atlas-mcp-headers.sh | sed -E 's/(Bearer )[^"]+/\1<redacted>/'
# {"Authorization":"Bearer <redacted>"}
```

### 2. Point the template at your install

Copy the template for your platform to `~/.config/atlas/atlas.mcp.json`, next to the token and
outside any checkout, and replace `https://atlas.example.com` with your install's URL. Keep the
`/mcp/` path and the trailing slash.

```sh
sed 's#https://atlas.example.com#https://atlas.yourcompany.example#' mcp.template.json > ~/.config/atlas/atlas.mcp.json
```

```json
{
  "mcpServers": {
    "atlas": {
      "type": "http",
      "url": "https://atlas.yourcompany.example/mcp/",
      "headersHelper": "sh \"$HOME/.config/atlas/atlas-mcp-headers.sh\""
    }
  }
}
```

The helper path is absolute on purpose: Claude Code runs a helper from a directory that depends
on where the server is configured. On Windows Claude Code runs the helper through `cmd.exe`,
which is why the Windows template says `%USERPROFILE%` rather than `$HOME`. Your token is used
for one server only; to reach a second install, mint a second token into a second file and pass
its path as the helper's argument (`... atlas-mcp-headers.sh" "$HOME/.config/atlas/other.token"`).

**If the install uses its own CA** (the default `deploy/oss` setup: `scripts/init-certs.sh`
makes one at `deploy/oss/secrets/tls/ca.crt`), Claude Code must trust it - see
[Trusting the install's CA](#trusting-the-installs-ca) below. A publicly trusted certificate
(for example one from ACME) needs nothing.

### 3. Add it to Claude Code

Either for one project - copy it in as that project's `.mcp.json` (Claude Code asks you to trust
the folder before it runs the helper):

```sh
cp ~/.config/atlas/atlas.mcp.json /path/to/your/project/.mcp.json
```

or for every project, at user scope:

```sh
claude mcp add-json atlas "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["mcpServers"]["atlas"]))' ~/.config/atlas/atlas.mcp.json)" --scope user
```

```powershell
# Windows, PowerShell 7.3 or later (earlier versions mangle the quotes)
claude mcp add-json atlas ((Get-Content "$HOME\.config\atlas\atlas.mcp.json" -Raw | ConvertFrom-Json).mcpServers.atlas | ConvertTo-Json -Compress) --scope user
```

Check the connection:

```sh
claude mcp list
# atlas: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected
```

In a session, `/mcp` shows the server and its tools. Check that `claude mcp get atlas` (or `/mcp`)
shows **your install's URL**: a project's `.mcp.json` that defines a server with the same name
takes precedence over the user-scope one, with no warning. Start `claude` outside such a project,
or add yours under another name.

`Failed to connect` with a certificate error means the CA step is missing, or Claude Code was not
restarted after it; a 401 means the token file is wrong, revoked or expired (run the helper by
hand, as in step 1).

## The plugin

The `atlas` plugin adds three skills:

- **`atlas-retrieval`** - how to read context out of Atlas cheaply: outline and search for
  pointers first, `atlas_structure` for the shape, `atlas_get` with `prose=summary` for detail,
  `atlas_doc_read` only to follow a procedure; and which calls return tens of kilobytes.
- **`file-task`** - file a workitem through `atlas_file_work`: find the parent cheaply, draft the
  acceptance for you to approve, never invent ids, read back the id the server minted.
- **`setup-guide`** - run the first-run setup guide with an install's owner through
  `atlas_setup_guide`: ask every question, never answer for them, record each topic as a
  Proposed ADR that only the owner, signed in, can accept. It needs an owner's token minted
  with `--scopes read,write,admin`.

Install it from a checkout of this repository:

```sh
claude plugin marketplace add ./clients/claude-code
claude plugin install atlas@atlas-client
```

or try it for one session without installing: `claude --plugin-dir ./clients/claude-code/plugin`.

## Trusting the install's CA

Copy the CA certificate (only the certificate, `ca.crt`, never `ca.key`) to the machine that runs
Claude Code, for example to `~/.config/atlas/ca.crt`. Then either:

- **Tell Claude Code about it** (recommended; affects nothing else): set `NODE_EXTRA_CA_CERTS` in
  the `env` block of `~/.claude/settings.json`, which also reaches background sessions:

  ```json
  { "env": { "NODE_EXTRA_CA_CERTS": "/home/you/.config/atlas/ca.crt" } }
  ```

  Then restart Claude Code: Node reads the variable only when the process starts, so `/mcp`
  reconnect in a running session is not enough. Or export it in the shell you start `claude`
  from (that shell only). `claude --debug` logs
  `CA certs: Appended extra certificates from NODE_EXTRA_CA_CERTS (...)` when it loaded.
- **Or add it to the operating system's trust store** (Claude Code reads the system store by
  default): Debian/Ubuntu `sudo cp ca.crt /usr/local/share/ca-certificates/atlas.crt && sudo
  update-ca-certificates`; macOS `sudo security add-trusted-cert -d -r trustRoot -k
  /Library/Keychains/System.keychain ca.crt`; Windows `Import-Certificate -FilePath ca.crt
  -CertStoreLocation Cert:\CurrentUser\Root`. This makes every program on the machine trust
  certificates that CA signs, so prefer the first option for a CA you do not control.

Never turn certificate checking off (`NODE_TLS_REJECT_UNAUTHORIZED=0`): the token would then go
to whoever answers.

## Using your IdP sign-in instead of a minted token

Where the install has turned it on (`ATLAS_OAUTH_RESOURCE`, ADR 0041 §9), Claude Code can sign
in to Atlas with an access token from your company IdP (Entra ID at work) instead of the
`atlas_svc_` token in step 1, which keeps working beside it. Add the server WITHOUT
`headersHelper` or `headers`: on the first 401 Claude Code reads the server's
`/.well-known/oauth-protected-resource`, which names the IdP, and `/mcp` -> Authenticate opens
the IdP's sign-in in your browser. Three things to know:

- **Sign in to the board with SSO once first.** A token for someone Atlas has no account for
  is refused with that instruction; Atlas never creates an account from a token.
- **The IdP must know Claude Code as a client** (Entra has no dynamic registration): your IdP
  admin registers a public client allowed to request the Atlas API's scope, and you give
  Claude Code that client id with its OAuth options for the server (`claude mcp add --help`).
- **What it may do** is the scope you were granted mapped by the install
  (`ATLAS_OAUTH_SCOPE_MAP`, read-only by default), never more than your role and your project
  roles allow. Atlas records it as an agent of yours, one per OAuth client, so its writes are
  attributed to you as they are with a minted token. To turn one client off, an operator
  disables that agent; disabling you turns off all of them.

## Rotating and revoking

Mint a new token with the same `--purpose` (it is added to the same agent), replace the file,
run `claude mcp list`, then revoke the old one: `atlas-admin token list --user <you>` and
`atlas-admin token revoke <id>`. Claude Code re-runs the helper on every connection and after a
401, so a replaced file takes effect on the next reconnect (`/mcp` -> reconnect).

## What CI checks

`tests/test_client_pack.sh` (the `client-pack` job in `.github/workflows/tools.yml`) checks the
JSON and the manifests (`tests/check_pack.py`, and `claude plugin validate --strict`), runs both
helpers against good and bad token files, starts a TLS stub of the MCP endpoint signed by a
throwaway CA, and connects the real Claude Code CLI to it with the template, the helper and
`NODE_EXTRA_CA_CERTS`. The stub is not Atlas: the connect against a live install is what the
steps above do. The scrub gate (`.github/workflows/scrub.yml`) is enforced on `clients/**`.
