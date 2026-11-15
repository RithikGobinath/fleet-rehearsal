# Release evidence and handoff

## Local portfolio candidate

Use the successful **Release candidate checks** run for the exact PR head. Each run includes the commit SHA. The checked-in scripts define the workload so a reviewer can rerun it from the same checkout.

| Evidence | Where to look |
| --- | --- |
| Hosted-scope rejection, successful canary, pending evidence, projected failure and zero simulated starts for the blocked plan | **inventory** logs and **browser-evidence** artifacts |
| Same-seed planner comparison, 10,000-asset HTTP p95 at 25 requests per second, Kafka duplicates and consumer interruption | **local-portfolio-evidence** JSON files |
| PostgreSQL backup and isolated restore, previous image rollout, worker restart and return to the candidate | **local-recovery-evidence** with candidate and previous source SHAs and measured local times |
| Image and dependency scans | **security-evidence** retains both Trivy reports, committed-secret scan and npm dependency scan |
| Repeatable walkthrough and operational investigation | [Portfolio walkthrough](portfolio-walkthrough.md), [operator guide](operator-guide.md), [operations runbook](operations.md) |

The safety gate requires green tests, compatible migrations, clean high/critical image scans, and unchanged simulated outcomes after retries. HTTP p95 below 300 ms is a performance target. Record actual results and discuss any miss before a hosted release. An EKS deployment needs separate evidence collected through its own ingress, identity and data recovery controls.

## Hosted decision

**Decision for this phase: $0 in AWS.** The release authorizes local portfolio verification. The AWS plan, apply, live checks, and hosted promotion remain pending. Use [the staging deployment steps](../deploy/README.md) and the [AWS sandbox proposal](aws-sandbox.md) when the 24-hour run is approved.

Before staging, record the account, region, release window, maintenance owner, budget and teardown UTC times. Verify the GitHub OIDC publisher can put the expected images into ECR and the deploy operator is a separately authorized identity. Validate certificate, DNS and Auth0 HTTPS origins and use fresh synthetic identities for the scoped smoke checks.

Capture the approved source SHA, both immutable image digests, ZIP digest, test artifacts, rollback reference, and measured backup/restore results. During staging, verify browser access boundaries, the healthy and blocked Harbor runs, observation delay, broker restart, dashboard, and cost exposure. On any failed safety or access check, hold promotion.

For production, keep separate AWS account, Terraform backend key and deploy role, PostgreSQL database and credentials, and Auth0 client and API audience. Use the staging-approved artifact digest. Move the same API and web digests through the migration and rollout steps. A first release with no verified rollback digest closes external ingress on a failed smoke check and preserves logs and the database for investigation. A later release can redeploy the last verified digest if the database is compatible. Production topology and account are pending the hosted decision.

Set aside a 15-minute observation window following promotion. Recheck scoped sign-in, API readiness, both Harbor paths, and observation lag. Hold or back out on cross-tenant visibility, an unexpected simulator start, repeated readiness failure, or pending observations older than 60 seconds for one minute. Record UTC start and stop, actor, migration job result, and rollback or follow-up next to the artifact.

The repository maintainer owns release and teardown decisions. The operator checks dependency updates monthly after hosting starts. Review the 24-hour staging teardown against the account inventory before retaining any artifacts or state. Publish the verified endpoint or local recording and the exact CI evidence when closing the milestone.

## Optional integrations

The local evaluator is fully synthetic. A future read-only MCP explanation should reuse the scoped audit response and preserve caller authorization. A future Cisco evidence connector needs an authorized source, allowed fields, traceable revisions, and a deterministic fixture fallback. For a real-device pilot, require a separate asset-owner decision, scoped lab access, review of data retention, and a human change and rollback owner. There is no pilot approval in this evidence.
