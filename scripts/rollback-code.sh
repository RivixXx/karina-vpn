#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 <git-ref>" >&2; exit 2; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
git diff --quiet && git diff --cached --quiet || {
  echo "Refusing rollback with a dirty working tree" >&2; exit 1;
}
target="$(git rev-parse --verify "${1}^{commit}")" || {
  echo "Invalid commit: $1" >&2; exit 1;
}
git checkout --detach "$target"
bash "$ROOT/scripts/deploy.sh"
echo "Code rolled back to $target; application and XUI data were not rolled back"
