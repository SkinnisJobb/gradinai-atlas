<#
.SYNOPSIS
  Claude Code `headersHelper` for an Atlas MCP server (PowerShell variant).

.DESCRIPTION
  Prints the one header Atlas needs, as the JSON object Claude Code expects on stdout:

      {"Authorization":"Bearer <your agent token>"}

  The token is read from a FILE, so it is never in .mcp.json, never on a command line and
  never in an environment variable. The file holds the raw token on its first line (what
  `atlas-admin token mint --out` writes).

  On Windows the file must not be readable by Everyone, Users or Authenticated Users; restrict
  it with icacls (see README.md). On Linux and macOS (pwsh 7.1+) it must not be readable by
  group or others (chmod 600). The helper refuses the file otherwise.

  Runs on Windows PowerShell 5.1 and PowerShell 7+.
  Exit status: 0 with the JSON on stdout, or 1 with a reason on stderr and nothing on stdout.

.PARAMETER TokenFile
  Path to the token file. Default: $HOME/.config/atlas/token
#>
param(
    [string]$TokenFile = (Join-Path (Join-Path (Join-Path $HOME '.config') 'atlas') 'token')
)

$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
    [Console]::Error.WriteLine("atlas-mcp-headers: $Message")
    exit 1
}

if (-not (Test-Path -LiteralPath $TokenFile)) {
    Fail "no token file at $TokenFile (mint one with: atlas-admin token mint --user <you> --purpose claude-code --out <file>)"
}
$item = Get-Item -LiteralPath $TokenFile -Force
if ($item.PSIsContainer) { Fail "$TokenFile is not a regular file" }

$onWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows
if ($onWindows) {
    # Everyone, BUILTIN\Users, Authenticated Users: a token file any of them can read is shared.
    $broad = @('S-1-1-0', 'S-1-5-32-545', 'S-1-5-11')
    $acl = Get-Acl -LiteralPath $TokenFile
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        try {
            $sid = $ace.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        } catch { continue }
        if ($broad -contains $sid) {
            Fail "$TokenFile is readable by $($ace.IdentityReference); restrict it with icacls (see README.md)"
        }
    }
} elseif ($null -ne $item.UnixMode) {
    # e.g. -rw------- ; characters 5-10 are the group and other bits.
    $bits = $item.UnixMode.Substring(4, 6)
    if ($bits -ne '------') {
        Fail "$TokenFile is readable or writable by group or others ($bits); run: chmod 600 $TokenFile"
    }
}

$first = Get-Content -LiteralPath $TokenFile -TotalCount 1
if ($null -eq $first) { Fail "$TokenFile is empty" }
$tok = ([string]$first).Trim()
if ($tok -eq '') { Fail "$TokenFile has no token on its first line" }
# Only token characters: anything else would break (or inject into) the JSON below.
if ($tok -notmatch '^[A-Za-z0-9._~+/=-]{16,512}$') {
    Fail "$TokenFile does not look like a token (unexpected characters or length)"
}

[Console]::Out.WriteLine('{"Authorization":"Bearer ' + $tok + '"}')
exit 0
