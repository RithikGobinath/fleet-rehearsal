import hashlib
import json
import subprocess
import sys
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from zipfile import ZipFile


registry = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fleet-rehearsal"
manifest = {
    "schema": 1, "source_sha": "a" * 40, "ci_run": 123,
    "images": {name: {"reference": f"{registry}/{name}@sha256:" + "a" * 64,
                      "scan_sha256": "b" * 64} for name in ("api", "web")},
}
script = Path(__file__).with_name("render-release.py")
with TemporaryDirectory() as directory:
    path = Path(directory) / "manifest.json"
    path.write_text(json.dumps(manifest))
    images = json.loads(subprocess.check_output([sys.executable, script, path]))["images"]
    assert [image["name"] for image in images] == ["fleet-api", "fleet-web"]
    assert all(image["digest"] == "sha256:" + "a" * 64 for image in images)
    for invalid in (f"{registry}/web:latest", f"{registry}/web@sha256:wrong", "docker.io/nginx:stable"):
        manifest["images"]["web"]["reference"] = invalid
        path.write_text(json.dumps(manifest))
        result = subprocess.run([sys.executable, script, path], capture_output=True)
        assert result.returncode != 0, invalid

    archive = Path(directory) / "release.zip"
    verify = run_path(str(script.with_name("prepare-promotion.py")))["verify"]
    report = b'{"Results":[{"Vulnerabilities":[]}]}'
    for name, image in manifest["images"].items():
        image.update(reference=f"{registry}/{name}@sha256:" + "a" * 64, scan_sha256=hashlib.sha256(report).hexdigest())
    with ZipFile(archive, "w") as evidence:
        evidence.writestr("manifest.json", json.dumps(manifest))
        for name in manifest["images"]:
            evidence.writestr(f"{name}-scan.json", report)
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    assert verify(archive, manifest["source_sha"], digest)["images"] == images
    for source, approved_digest in (("c" * 40, digest), (manifest["source_sha"], "sha256:" + "0" * 64)):
        try:
            verify(archive, source, approved_digest)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted an unapproved release archive.")
