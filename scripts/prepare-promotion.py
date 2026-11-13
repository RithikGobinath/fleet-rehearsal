import argparse
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile

from runpy import run_path

render = run_path(str(Path(__file__).with_name("render-release.py")))["render"]


def verify(artifact, source, digest):
    with artifact.open("rb") as archive:
        if f"sha256:{hashlib.file_digest(archive, 'sha256').hexdigest()}" != digest:
            raise ValueError("The archive does not match the approved GitHub artifact digest.")
        archive.seek(0)
        with ZipFile(archive) as release:
            manifest = json.loads(release.read("manifest.json"))
            reports = {name: release.read(f"{name}-scan.json") for name in ("api", "web")}
    if source != manifest["source_sha"]:
        raise ValueError("The approved source does not match the release manifest.")
    overlay = render(manifest)
    for name, image in manifest["images"].items():
        report = reports[name]
        if hashlib.sha256(report).hexdigest() != image["scan_sha256"]:
            raise ValueError(f"The {name} scan does not match the release manifest.")
        results = json.loads(report).get("Results")
        if not results or any(item.get("Severity") in ("HIGH", "CRITICAL")
                              for result in results for item in result.get("Vulnerabilities") or []):
            raise ValueError(f"The {name} image did not pass the release scan.")
    return overlay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("environment", choices=("staging", "production"))
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args()
    overlay = verify(args.artifact, args.source, args.digest)
    target = Path(__file__).resolve().parents[1] / "deploy" / f"release-{args.environment}"
    target.mkdir(exist_ok=True)
    path = target / "kustomization.yaml"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    print(path)


if __name__ == "__main__":
    main()
