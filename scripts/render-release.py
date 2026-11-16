import argparse
import json
import re
from pathlib import Path


def render(manifest):
    if manifest.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("source_sha", "")):
        raise ValueError("Invalid release source.")
    if not isinstance(manifest.get("ci_run"), int) or manifest["ci_run"] <= 0:
        raise ValueError("Missing release CI run.")
    images = manifest.get("images", {})
    if set(images) != {"api", "web"}:
        raise ValueError("A release must include exactly the API and web images.")
    entries = []
    registries = set()
    for name, image in sorted(images.items()):
        match = re.fullmatch(r"(\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com)/fleet-rehearsal/"
                             + name + r"@(sha256:[0-9a-f]{64})", image.get("reference", ""))
        if not match or not re.fullmatch(r"[0-9a-f]{64}", image.get("scan_sha256", "")):
            raise ValueError(f"Missing immutable image or scan evidence for {name}.")
        registry, digest = match.groups()
        registries.add(registry)
        entries.append({"name": f"fleet-{name}", "newName": f"{registry}/fleet-rehearsal/{name}", "digest": digest})
    if len(registries) != 1:
        raise ValueError("The release images must use the same registry.")
    return {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "resources": ["../base"], "images": entries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(render(json.loads(args.manifest.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
