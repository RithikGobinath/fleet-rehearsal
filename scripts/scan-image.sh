#!/usr/bin/env bash
set -euo pipefail

image=$1
report=$2
scanner=ghcr.io/aquasecurity/trivy:0.69.3@sha256:bcc376de8d77cfe086a917230e818dc9f8528e3c852f7b1aff648949b6258d1c
mkdir -p "$(dirname "$report")"
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
docker image save -o "$archive" "$image"
docker run --rm -v "$archive:/image.tar:ro" "$scanner" image --input /image.tar --scanners vuln --format json --quiet > "$report"
jq -e '(.Results | length) > 0 and ([.Results[]?.Vulnerabilities[]? | select(.Severity == "HIGH" or .Severity == "CRITICAL")] | length == 0)' "$report"
