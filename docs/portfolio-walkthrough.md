# Fleet Rehearsal in 90 seconds

Open `portfolio.html` from **local-portfolio-evidence** for the captioned walkthrough. Play the rehearsal WebM inside **browser-evidence** to see the live interaction. The recording is browser automation with no narration. The voiceover below can accompany it in an interview. Use the measured Prometheus sample from the same Actions run for the operations segment.

| Time | Screen | Voiceover |
| --- | --- | --- |
| 0:00–0:12 | Harbor inventory with affected, unaffected and needs-evidence filters | “Before changing a network, an operator needs to know which devices need attention, what the evidence says and who can act. This fleet is synthetic.” |
| 0:12–0:32 | `approved-wave.png` and `passing-wave.png` | “A Harbor partner proposes a canary. A different user approves that plan revision. Only a scoped operator can start it. With both uplinks healthy, the simulated canary succeeds.” |
| 0:32–0:55 | `pending-observation.png` | “In a second plan, the peer goes down after approval. PostgreSQL has accepted the observation, but Kafka projection is pending. The start is blocked before a run is created.” |
| 0:55–1:14 | `blocked-wave.png` and the audit timeline | “The projector applies the peer failure. A fresh intent returns `alternate_path_unavailable`. The audit trail retains both denials. The browser test confirms zero simulator starts for this plan.” |
| 1:14–1:25 | `prometheus.txt` from **local-portfolio-evidence**, or the **Blocked rehearsal starts and failed verifications** Grafana panel from the same run | “Operators can distinguish an intentional safety stop from lag. The counters group stops by reason, and the request ID connects the API response and the audit event.” |
| 1:25–1:30 | Exact GitHub Actions commit and release gate | “The same code is exercised against PostgreSQL and Kafka and deployed into a disposable Kubernetes cluster.” |

The benchmark compares completed work and stopped waves on the same seeded inputs. Use its raw results when discussing tradeoffs. The local run proves the application and its Kubernetes packaging. The future EKS run exercises the cloud identity, ingress, and managed database, which remain pending.

Reproduce the recording using the [operator guide](operator-guide.md). The Playwright screenshots, video and trace are in its rehearsal result directory.
