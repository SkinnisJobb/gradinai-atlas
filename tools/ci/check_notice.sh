#!/usr/bin/env bash
# check_notice.sh: LICENSE and NOTICE exist, and NOTICE names every vendored file (STORY-oss-22).
#
# The rule.
#   - LICENSE is the unmodified Apache License 2.0 text (sha256 of the canonical
#     https://www.apache.org/licenses/LICENSE-2.0.txt).
#   - NOTICE exists and names every vendored file by its repo-relative path. A vendored file is
#     any tracked file under a `vendor/` directory, or any tracked `*.min.js` / `*.min.css`.
# Adding a vendored library therefore fails CI until its NOTICE entry (name, version, copyright
# line, licence) is written.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

APACHE_SHA256=cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30
fail=0

if [ ! -f LICENSE ]; then
  echo "FAIL: LICENSE is missing"; fail=1
elif [ "$(sha256sum LICENSE | cut -d' ' -f1)" != "$APACHE_SHA256" ]; then
  echo "FAIL: LICENSE is not the unmodified Apache License 2.0 text"; fail=1
fi

if [ ! -f NOTICE ]; then
  echo "FAIL: NOTICE is missing"; exit 1
fi

n=0
while IFS= read -r f; do
  n=$((n + 1))
  if ! grep -qxF "$f" NOTICE; then
    echo "FAIL: vendored file not listed in NOTICE: $f"; fail=1
  fi
done < <(git ls-files | grep -E '(^|/)vendor/|\.min\.(js|css)$' | sort -u)

[ "$fail" -eq 0 ] && echo "ok: LICENSE is Apache-2.0; NOTICE lists all $n vendored file(s)"
exit "$fail"
