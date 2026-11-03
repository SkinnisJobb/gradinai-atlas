# Security policy

## Supported versions

| Version | Supported |
|---|---|
| The latest release | Yes. A fix ships as a new release, not as a patch to an older one. |
| `master` | Yes, before the first release and between releases. |
| Older releases | No. Upgrade to the latest release. |

## Reporting a vulnerability

Please do not open a public issue, discussion or pull request for a security problem.

Report it privately through GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**
([direct link](https://github.com/SkinnisJobb/gradinai-atlas/security/advisories/new)). The
report is visible only to you and the maintainer. There is no security mailbox.

Include:

- the affected version or commit;
- the component (`atlas-api`, `atlas-mcp`, the board or the install);
- steps to reproduce, and what an attacker gains;
- whether the problem needs a non-default setting.

Do not include real tokens, passwords or production data. If a credential was exposed while you were
investigating, say so, so it can be revoked.

You will get an acknowledgement, and a fix or mitigation will be coordinated with you in the
advisory before anything is disclosed publicly.

## The security model

What Atlas protects, the controls in the code, the hardening an operator is expected to apply and
the security checks that run in CI are described in [docs/oss/SECURITY.md](docs/oss/SECURITY.md).
