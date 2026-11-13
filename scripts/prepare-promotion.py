import argparse
import hashlib
import json
import os
from pathlib import Path

from runpy import run_path

render = run_path(str(Path(__file__).with_name("render-release.py")))["render"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("environment", choices=("staging", "production"))
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    manifest = json.loads((args.evidence / "manifest.json").read_text(encoding="utf-8"))
    if args.source != manifest["source_sha"]:
        parser.error("The approved source does not match the release manifest.")
    overlay = render(manifest)
    for name, image in manifest["images"].items():
        report = (args.evidence / f"{name}-scan.json").read_bytes()
        if hashlib.sha256(report).hexdigest() != image["scan_sha256"]:
            parser.error(f"The {name} scan does not match the release manifest.")
        results = json.loads(report).get("Results")
        if not results or any(item.get("Severity") in ("HIGH", "CRITICAL")
                              for result in results for item in result.get("Vulnerabilities") or []):
            parser.error(f"The {name} image did not pass the release scan.")
    target = Path(__file__).resolve().parents[1] / "deploy" / f"release-{args.environment}"
    target.mkdir(exist_ok=True)
    path = target / "kustomization.yaml"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    print(path)


if __name__ == "__main__":
    main()
