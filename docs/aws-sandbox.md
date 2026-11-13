# AWS staging proposal

| Budget option | What it proves | Tradeoff |
| --- | --- | --- |
| $0 in AWS spend, local and existing GitHub Actions | A reproducible synthetic rehearsal with scoped approval, a safe stop after a peer failure, audit history, and a Kubernetes smoke test. | Reviewers run it locally or watch a recording. AWS deployment and identity remain unverified. |
| $12/month for 2 GB or $24/month for 4 GB Lightsail | A persistent browser demo. Start with 4 GB if the VM runs the full stack, including Kafka and PostgreSQL. | The operator maintains the host, backups, and HTTPS. This path provides no EKS, RDS, or release-role evidence. |
| About $12 estimated for one 24-hour EKS run | AWS deployment, private RDS, GitHub OIDC, ECR digests, and teardown evidence. | The endpoint expires after the sandbox window. Actual spend depends on runtime and usage. |
| Always-on EKS, about $73/month for standard-support control plane alone | A persistent managed Kubernetes endpoint. | Worker, database, networking, and storage increase the monthly cost. |

Use the local demo and CI evidence for the portfolio. Add a short AWS run when the candidate is green and there is a reason to show the cloud controls. Use a persistent VM only when reviewers need a live link. Reference prices are available for [Lightsail](https://aws.amazon.com/lightsail/pricing/) and [EKS](https://aws.amazon.com/eks/pricing/).

Proposed region: `us-east-1`. Use a dedicated sandbox account, one EKS cluster on standard support, one On-Demand `m6i.large` worker node in a private subnet, and one private Single-AZ PostgreSQL RDS instance. One NAT gateway provides outbound access. One internal Kafka broker uses persistent storage on the node's Availability Zone. An ACM certificate and an approved hostname are needed for TLS ingress and the Auth0 callback.

| Line item | Budget allowance for 24 hours |
| --- | ---: |
| EKS control plane | $2.40 |
| EC2 and node disk | $3.00 |
| RDS and storage | $1.50 |
| NAT, its IPv4 address, and limited data | $2.00 |
| TLS load balancer, public IPv4, and light traffic | $1.50 |
| Kafka disk, registry storage, logs, and DNS | $1.60 |
| **Estimated total** | **$12** |

The EC2, database, and network lines are planning allowances. Refresh the estimate in the [AWS Pricing Calculator](https://calculator.aws/) using the chosen account's region and actual data volumes before applying. Reference prices include [EKS standard support](https://aws.amazon.com/eks/pricing/), [VPC and IPv4](https://aws.amazon.com/vpc/pricing/), [load balancing](https://aws.amazon.com/elasticloadbalancing/pricing/), and [RDS PostgreSQL](https://aws.amazon.com/rds/postgresql/pricing/).

Proposed authorization: one 24-hour staging window, $25 spending ceiling, alert at $15 and $25, and teardown by the end of the window. AWS Budgets sends alerts and does not enforce a hard spending limit. The account owner retains the bootstrap and teardown identity. The owner records the account ID, UTC start and teardown, alert recipient, ingress hostname and certificate, and approved administrator network before an AWS plan or apply. Keep state in a separately managed private, versioned, encrypted S3 bucket with Terraform lockfiles enabled.

After smoke checks, delete the load balancer service and wait for the load balancer to disappear. Remove the workload namespace, then destroy the staging stack. Check that EC2, RDS, NAT, volumes, and load balancers are gone. Keep only explicitly approved state and retained evidence.
