---
audience: operations-security
---

# OpenShift: creating the peppers and backing them up

This page is for an install on OpenShift (Azure Red Hat OpenShift) whose secrets are plain
Secrets made by a script, not delivered from Azure Key Vault by the External Secrets Operator. The
design is in [Atlas on Azure Red Hat OpenShift](../research/openshift-aro.md), question 7. The
full OpenShift install guide is still to come; this page covers only the secrets step.

Atlas has two **peppers**: random keys mixed into every password verifier (`password_pepper`) and
every service-token digest (`token_pepper`). They are created once and never change. New peppers
sign every account out and make every token useless, and a database backup restored without its
peppers cannot be signed in to. So the peppers need a backed-up home outside the cluster.

## Create the peppers and the Secrets

`scripts/openshift/new-secrets.py` uses only Python's standard library and starts no shell, so the
same command works in PowerShell 7 and in bash. Log in with `oc login` first, then, from the
repository root:

```
python scripts/openshift/new-secrets.py -n atlas --dry-run
python scripts/openshift/new-secrets.py -n atlas
```

The first command prints the `oc` commands and changes nothing. The second writes
`secrets/openshift/password_pepper` and `secrets/openshift/token_pepper` (32 random bytes each, in
base64), then runs `oc create secret generic atlas-peppers --from-file=...`. Only the file paths
are passed to `oc`, which reads the files itself: no value appears on a command line, in an
environment variable or in the script's output. The repository's `.gitignore` already covers any
`secrets/` directory.

The Secrets it creates, under the names the manifests mount:

| Secret | Keys | When |
|---|---|---|
| `atlas-peppers` | `password_pepper`, `token_pepper` | always |
| `atlas-oidc` | `client_secret` or `client_key` | with `--oidc-client-secret FILE` or `--oidc-client-key FILE` (component `oidc`) |
| `atlas-embed` | `key` | with `--embed-key FILE` (component `retrieval`, key mode) |

For `atlas-oidc` and `atlas-embed` you supply the file (the value comes from your identity
provider or your embeddings service); the script only puts it in the Secret.

The script refuses to run again over existing pepper files or an existing Secret. `--reuse` builds
the Secret from pepper files you already have (a reinstall, or files restored from Key Vault).
`--force` makes new peppers and replaces the Secrets, which is only safe on an install with no
data yet. `--help` lists every option.

**Protecting the files.** On Linux and macOS the script makes the directory `0700` and each file
`0600`, so only you can read them. On Windows those modes mean nothing; the directory's access
control list (ACL) is what keeps other users out. Keep the output directory under your own user
profile, or restrict it before running the script, for example:

```
icacls secrets\openshift /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F"
```

## Back the peppers up to Azure Key Vault

Store each pepper in the vault straight from its file. `--file` makes the Azure CLI read the value
from the file, so it never appears on the command line (Key Vault names allow only letters, digits
and dashes):

```
az keyvault secret set --vault-name <vault> --name atlas-password-pepper --file secrets/openshift/password_pepper --encoding utf-8
az keyvault secret set --vault-name <vault> --name atlas-token-pepper --file secrets/openshift/token_pepper --encoding utf-8
```

The vault should have soft delete and purge protection on (`az keyvault update --name <vault>
--enable-purge-protection true`), so a deleted secret can be recovered and cannot be purged early,
and its diagnostic logs should go to your organisation's log store, so every read is recorded.

Record the SHA-256 fingerprint of each file in the change record for the install. A fingerprint
of 32 random bytes reveals nothing usable, and it lets you prove later that the vault, the files
and the running pods hold the same peppers. `scripts/openshift/pepper-fingerprint.py` prints one
line per file, `<name>  sha256:<hex>`, and never the value; save its output as the record:

```
python scripts/openshift/pepper-fingerprint.py secrets/openshift/password_pepper secrets/openshift/token_pepper > pepper-fingerprints.txt
```

The fingerprint is the SHA-256 of the file's bytes exactly as written, the base64 text plus its
trailing newline, so `sha256sum` (bash) and `Get-FileHash -Algorithm SHA256` (PowerShell) on the
same files give the same hex, an independent cross-check. The script also refuses a file that is
not base64 of 32 bytes, without showing it.

Once the vault holds both peppers, delete the local files, or keep them only somewhere as
protected as the vault.

## Restore the peppers from Key Vault

To recreate the Secret (a new namespace, a restore drill), download the peppers into an empty
directory and build the Secret from them. `az keyvault secret download` writes the value to the
file, not to the screen, and refuses to overwrite an existing file:

```
az keyvault secret download --vault-name <vault> --name atlas-password-pepper --file secrets/openshift/password_pepper --encoding utf-8
az keyvault secret download --vault-name <vault> --name atlas-token-pepper --file secrets/openshift/token_pepper --encoding utf-8
python scripts/openshift/new-secrets.py -n atlas --reuse
```

`--reuse` checks that each file holds base64 of 32 bytes before it creates `atlas-peppers`.

## The restore drill: prove the peppers match

The fingerprints recorded at creation are checked at two points, and the drill fails at the first
mismatch. `pepper-fingerprint.py` exits non-zero on a missing or malformed file, and with
`--compare` on any fingerprint that differs from the record.

**1. Check the restored files before `--reuse`.** After the `az keyvault secret download` lines
above, and before `new-secrets.py --reuse`:

```
python scripts/openshift/pepper-fingerprint.py --compare pepper-fingerprints.txt secrets/openshift/password_pepper secrets/openshift/token_pepper
```

A mismatch on a file that is otherwise a valid pepper usually means its line ending changed on the
way (no trailing newline, or CRLF); the script says which, never the value. Do not edit the file to
make it match: work out which copy is the right one first.

**2. Check the pods after deploy.** The image has Python but no copy of the script, and its root
filesystem is read-only, so feed the script to Python on standard input. It reads the Secret
mounted at `/run/secrets/peppers/` and writes nothing. In bash:

```
oc exec -i -n atlas deploy/atlas-api -- python - < scripts/openshift/pepper-fingerprint.py > pod-fingerprints.txt
```

PowerShell has no `<` redirection, so pipe the file instead:

```
Get-Content -Raw scripts/openshift/pepper-fingerprint.py | oc exec -i -n atlas deploy/atlas-api -- python - > pod-fingerprints.txt
```

Add `-c atlas-api` after `deploy/atlas-api` if the pod has more than one container. Then compare
the pod's list with the record, on your machine:

```
python scripts/openshift/pepper-fingerprint.py --compare pepper-fingerprints.txt --from-list pod-fingerprints.txt
```

`--compare` reads this script's output or `sha256sum`'s. Keep both lists, and the commands' exit
status, with the drill's evidence: they show that the vault, the restored files and the running
pods hold the same peppers, and no one saw a value.
