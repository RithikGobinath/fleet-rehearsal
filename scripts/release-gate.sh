#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo 'Release verification requires explicit job results.' >&2
  exit 1
fi
for result in "$@"; do
  if [[ "$result" != success ]]; then
    echo "Required job result: $result" >&2
    exit 1
  fi
done
