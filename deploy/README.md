# Staging deployment

The [sandbox proposal](../docs/aws-sandbox.md) records the expected resources and approval inputs. Validate Terraform without an AWS backend using `terraform -chdir=infra/staging init -backend=false` followed by `terraform -chdir=infra/staging validate`. After account approval, the owner initializes the approved S3 backend, saves and reviews a plan, and applies the reviewed plan with the staging bootstrap identity.

The bootstrap owner uses the RDS bootstrap secret to create a separate database login that owns only the `fleet` database and its schema. Store that connection string with `sslmode=require` and the three public Auth0 settings in the `fleet-runtime-secrets` Kubernetes Secret. The EKS endpoint is limited to the approved operator network. The publisher role has ECR access and no EKS access. Deploy from the approved operator network using a short-lived AWS identity mapped to the cluster.

Register an Auth0 SPA and an RS256 API for the staging hostname. Use Auth0's default access token profile, which supplies the `azp` claim. Allow only the exact HTTPS origin for callbacks, logout and web origins. Provision each reviewed `(issuer, subject, principal_id)` mapping in `hosted_identities` and review the matching database grants. Auth0 subjects are case-sensitive. Provision different users for the partner, approver and operator walkthroughs.

Set the repository variables `FLEET_AWS_REGION`, `FLEET_ECR_REGISTRY`, and `FLEET_PUBLISH_ROLE_ARN` after the AWS prerequisites are in place. Enable the publisher with `FLEET_RELEASE_ENABLED=true` for the approved window. On a passing main push, it stores the source, ECR digests and matching scan reports in a GitHub artifact. Download the artifact from the publisher run for the reviewed commit.

Run `python scripts/prepare-promotion.py staging <artifact-directory> --source <reviewed-sha>` to generate the staging overlay. Review with `kubectl kustomize deploy/release-staging` before applying with the staging context. For production, give the same artifact and source to `prepare-promotion.py production` and use the separately approved production context and credentials. This path verifies the source, digests, and scan report hashes. The worker, relay and projector reuse the API digest.

For external staging access, use the approved AWS load balancer integration. Set `aws-load-balancer-ssl-cert` to the reviewed ACM certificate ARN and apply `staging/ingress.yaml` after the base is ready. Register the chosen DNS name only after the TLS endpoint is healthy. Use the same immutable manifest when preparing production and select the production cluster with a separate human release identity.

The single Kafka broker can restart and reuse its disk. It has no replica and cannot provide availability after a disk or Availability Zone loss. PostgreSQL stores the accepted observations. Preserve it and reconcile unprocessed events before replaying after broker loss.
