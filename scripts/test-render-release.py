import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


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
