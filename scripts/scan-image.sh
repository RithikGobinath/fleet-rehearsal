#!/usr/bin/env bash
set -euo pipefail

image=$1
report=$2
scanner=ghcr.io/aquasecurity/trivy:0.69.3@sha256:bcc376de8d77cfe086a917230e818dc9f8528e3c852f7b1aff648949b6258d1c
mkdir -p "$(dirname "$report")"
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock +  "$scanner" image --scanners vuln --format json --quiet "$image" > "$report"
jq -e '[.Results[]?.Vulnerabilities[]? | select(.Severity == "HIGH" or .Severity == "CRITICAL")] | length == 0' "$report"
