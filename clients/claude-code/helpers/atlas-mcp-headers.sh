#!/bin/sh
# atlas-mcp-headers.sh - Claude Code `headersHelper` for an Atlas MCP server.
#
# Prints the one header Atlas needs, as the JSON object Claude Code expects on stdout:
#
#     {"Authorization":"Bearer <your agent token>"}
#
# The token is read from a FILE, so it is never in .mcp.json, never on a command line and
# never in an environment variable:
#
#     atlas-mcp-headers.sh [TOKEN_FILE]        default: $HOME/.config/atlas/token
#
# The file holds the raw token on its first line (what `atlas-admin token mint --out` writes).
# On Linux and macOS it must not be readable by group or others (chmod 600), the same rule
# ssh applies to a private key; the helper refuses it otherwise. On Windows (Git Bash, MSYS,
# Cygwin) file modes are not real, so restrict the file with icacls instead (see README.md).
#
# Why a file and not a variable: Claude Code removes environment variables whose names look
# like credentials (TOKEN, KEY, SECRET, AUTH, ...) before it runs a helper declared in a
# project .mcp.json, and an exported token leaks into every child process anyway.
#
# Exit status: 0 with the JSON on stdout, or 1 with a reason on stderr and nothing on stdout.
# Plain POSIX sh: dash, bash, zsh and busybox ash all run it. `printf` is a shell builtin in
# each of them, so the token never becomes a process argument.

set -u

die() {
    printf 'atlas-mcp-headers: %s\n' "$*" >&2
    exit 1
}

file=${1:-${HOME:?HOME is not set}/.config/atlas/token}

[ -e "$file" ] || die "no token file at $file (mint one with: atlas-admin token mint --user <you> --purpose claude-code --out <file>)"
[ -f "$file" ] || die "$file is not a regular file"
[ -r "$file" ] || die "$file is not readable by this user"

case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*)
        : # NTFS: the mode bits ls prints are synthesised; icacls is the real gate.
        ;;
    *)
        # Characters 5-10 of `ls -l` are the group and other permission bits.
        mode=$(ls -ld -- "$file" 2>/dev/null | cut -c5-10)
        case "$mode" in
            ------) ;;
            *) die "$file is readable or writable by group or others ($mode); run: chmod 600 $file" ;;
        esac
        ;;
esac

tok=
IFS= read -r tok < "$file" || [ -n "$tok" ] || die "$file is empty"
# Tolerate a file saved with Windows line endings or trailing blanks.
cr=$(printf '\r')
tok=${tok%"$cr"}
tok=${tok%%[[:space:]]*}

[ -n "$tok" ] || die "$file has no token on its first line"
# Only token characters: anything else would break (or inject into) the JSON below.
case "$tok" in
    *[!A-Za-z0-9._~+/=-]*) die "$file does not look like a token (unexpected characters)" ;;
esac
[ "${#tok}" -ge 16 ] || die "$file does not look like a token (too short)"
[ "${#tok}" -le 512 ] || die "$file does not look like a token (too long)"

printf '{"Authorization":"Bearer %s"}\n' "$tok"
