# Staging deployment

The [sandbox proposal](../docs/aws-sandbox.md) records the expected resources and approval inputs. Validate Terraform without an AWS backend using `terraform -chdir=infra/staging init -backend=false` followed by `terraform -chdir=infra/staging validate`. After account approval, the owner initializes the approved S3 backend, saves and reviews a plan, and applies the reviewed plan with the staging bootstrap identity.

The bootstrap owner uses the RDS bootstrap secret to create a separate database login that owns only the `fleet` database and its schema. Store that connection string with `sslmode=require` and the three public Auth0 settings in the `fleet-runtime-secrets` Kubernetes Secret. The EKS endpoint is limited to the approved operator network. The publisher role has ECR access and no EKS access. Deploy from the approved operator network using a short-lived AWS identity mapped to the cluster.

Register an Auth0 SPA and an RS256 API for the staging hostname. Allow only the exact HTTPS origin for callbacks, logout and web origins. Provision each reviewed `(issuer, subject, principal_id)` mapping in `hosted_identities` and review the matching database grants. Auth0 subjects are case-sensitive. Provision different users for the partner, approver and operator walkthroughs.

The release manifest records the source commit, immutable API and web image digests, and scan evidence. Use [the manifest renderer](../scripts/render-release.py) to select the images for both environments. The worker, relay, and projector reuse the same API digest. The `fleet` namespace, service accounts, internal services, network policies and persistent Kafka workload are defined in `base/`.

For external staging access, use the approved AWS load balancer integration. Set `aws-load-balancer-ssl-cert` to the reviewed ACM certificate ARN and apply `staging/ingress.yaml` after the base is ready. Register the chosen DNS name only after the TLS endpoint is healthy. Use the same immutable manifest when preparing production and select the production cluster with a separate human release identity.

The single Kafka broker can restart and reuse its disk. It has no replica and cannot provide availability after a disk or Availability Zone loss. PostgreSQL stores the accepted observations. Preserve it and reconcile unprocessed events before replaying after broker loss.
