"""Build a portable walkthrough from a disposable CI run's real artifacts."""

import argparse
import base64
import json
import re
from html import escape
from pathlib import Path


SHOTS = (
    ("approved-wave", "Review and approve", "The Harbor partner proposes a canary. A separate approver authorizes the same revision."),
    ("passing-wave", "Complete the canary", "With both uplinks healthy, the Northstar operator starts one successful synthetic run."),
    ("pending-observation", "Stop while evidence is pending",
     "The peer fails after approval. The operator's start is denied while Kafka projection is pending."),
    ("blocked-wave", "Keep the wave blocked", "The projected outage removes the alternate path. The audit records both denials and the blocked plan has zero simulator starts."),
)
STYLE = """
:root { color-scheme:light; font-family:system-ui,sans-serif; color:#17243b; background:#edf2f5 }
body { margin:0 } main { max-width:76rem; margin:auto; padding:clamp(1rem,4vw,3rem) }
header { color:#fff; background:#102339; padding:clamp(1rem,4vw,3rem); border-radius:1rem }
h1 { font-size:clamp(2rem,5vw,3.6rem); margin:0 } h2,p { margin:.25rem 0 .75rem }
code { line-break:anywhere } a,summary { color:#005f57; font-weight:650 }
a:focus-visible,summary:focus-visible,[tabindex]:focus-visible { outline:3px solid #057268; outline-offset:3px }
nav { display:flex; flex-wrap:wrap; gap:.75rem 1.25rem; margin:1.25rem 0 }
section { background:#fff; padding:clamp(1rem,3vw,2rem); border:1px solid #d5e0e8; border-radius:1rem; margin:1rem 0 }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(11rem,1fr)); gap:.75rem; margin:1rem 0 }
.metric { background:#edf7f6; padding:.75rem; border-radius:.5rem; margin:0 } .metric strong { display:block; color:#005f57 }
summary { cursor:pointer } .capture { overflow:auto; max-height:75vh; margin-top:.75rem; border:1px solid #d5e0e8 }
img { display:block; min-width:46rem; width:100%; height:auto }
"""


def render(screenshots, reports):
    sha = (reports / "source-sha.txt").read_text(encoding="utf-8").strip()
    api = json.loads((reports / "api-latency.json").read_text(encoding="utf-8"))
    replay = json.loads((reports / "kafka-replay.json").read_text(encoding="utf-8"))
    planner = json.loads((reports / "planner.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or api["source_sha"] != sha or replay["git_sha"] != sha:
        raise ValueError("The API, Kafka, and candidate reports must refer to the same commit.")

    cards = []
    for name, title, caption in SHOTS:
        matches = list(screenshots.rglob(f"{name}.png"))
        if len(matches) != 1 or matches[0].stat().st_size > 5_000_000:
            raise ValueError(f"Expected one Playwright screenshot under 5 MB for {name}.")
        picture = matches[0].read_bytes()
        if not picture.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Invalid PNG for {name}.")
        encoded = base64.b64encode(picture).decode("ascii")
        cards.append(f'<section id="{name}"><h2>{escape(title)}</h2><p>{escape(caption)}</p>'
                     f'<details open><summary>Browser capture</summary><div class="capture" tabindex="0" '
                     f'aria-label="{escape(title)} screenshot"><img src="data:image/png;base64,{encoded}" '
                     f'alt="{escape(title)} in the Fleet Rehearsal console"></div></details></section>')

    metrics = (
        ("HTTP response p95", f'{float(api["p95_response_ms"]):.1f} ms at {float(api["observed_requests_per_second"]):g} req/s'),
        ("Kafka deliveries", f'{int(replay["kafka_records"]):,}'),
        ("Duplicate deliveries", f'{int(replay["duplicate_records"]):,}'),
        ("Logical starts in replay test", int(replay["simulator_starts"])),
        ("Synthetic planner sites", int(planner["sites_per_seed"]) * len(planner["runs"])),
    )
    timing = ("The measured workload met the latency target." if api["target_met"] else
              "The latency target was missed or the request pacing was inconclusive. See the raw report.")
    stats = "".join(f'<p class="metric"><strong>{escape(str(value))}</strong>{escape(label)}</p>'
                    for label, value in metrics)
    nav = "".join(f'<a href="#{name}">{escape(title)}</a>' for name, title, _ in SHOTS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Rehearsal walkthrough</title><style>{STYLE}</style></head>
<body><main><header><h1>Fleet Rehearsal</h1><p>Synthetic Harbor maintenance on a disposable local stack.</p>
<code>{sha}</code></header><nav aria-label="Walkthrough steps">{nav}</nav>
{''.join(cards)}<section><h2>Measured workload</h2><div class="metrics">{stats}</div><p>{timing}</p>
<p>Loopback HTTP and test broker evidence from GitHub Actions.</p></section></main></body></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        args.output.write_text(render(args.screenshots, args.reports), encoding="utf-8")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Could not render portfolio evidence: {error}\n")
