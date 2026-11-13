# AWS staging proposal

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
