# CLAUDE.md

@AGENTS.md

Claude Code wiring only; `AGENTS.md`, imported above, is the briefing. Keep the import: Claude Code
loads this file automatically and `AGENTS.md` only through it.

- Measure the result yourself. A subagent that reports "could not verify" has verified nothing;
  run the checks in `AGENTS.md`'s done list before you call the work finished.
- The nested `backend/CLAUDE.md` and `frontend/CLAUDE.md` load when you read a file in that
  directory. Put directory-specific guidance there, not in this file.
- `clients/claude-code/` is the client pack for connecting Claude Code to an Atlas install as an
  MCP client. Working on this repository does not need one.
