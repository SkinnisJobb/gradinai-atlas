# Contributing to Atlas

Thanks for your interest. Bug reports, fixes and improvements are welcome.

## Who can do what

- **Anyone** may open an issue or a pull request.
- **Only the maintainer merges.** A pull request is reviewed, and merged when it is ready and CI
  passes. Review comments are part of that, not a rejection.

## How to contribute

1. **Open an issue first** for anything larger than a small fix, so the approach can be agreed
   before you spend time on it.
2. **Fork the repository and branch from `master`.** Keep one change per pull request.
3. **Run the checks** that cover what you touched, and add or update tests with the change. The
   [development guide](docs/oss/CONTRIBUTING.md) has the setup, the test suite, the CI jobs and
   the conventions the suite enforces.
4. **Open a pull request against `master`** describing what changed, why, and how you tested it.

Please do not include secrets, credentials, real hostnames or personal data in commits, issues or
pull requests. Report a security problem privately, as [SECURITY.md](SECURITY.md) describes, not in
an issue or a pull request.

## Licence of contributions

Atlas is licensed under the [Apache License, Version 2.0](LICENSE). Contributions are made under the
same licence (inbound = outbound): by opening a pull request, you agree that your contribution is
licensed under the Apache License 2.0, as described in its section 5. No sign-off line or separate
agreement is needed.

Do not add licence headers to source files; the repository-level `LICENSE` and `NOTICE` cover them.

If your change adds third-party code (a vendored library, a copied file), say so in the pull
request and add its name, version, copyright line and licence to `NOTICE`. Its licence must be
compatible with Apache-2.0.
