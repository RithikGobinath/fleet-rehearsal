locals {
  name          = "fleet-rehearsal-staging"
  github        = "RithikGobinath/fleet-rehearsal"
  github_repo   = "1453141406"
  github_prefix = "repo:RithikGobinath@116565536/fleet-rehearsal@${local.github_repo}"
  prefixes      = toset(["api", "web"])
}

data "aws_availability_zones" "available" {
  state = "available"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.6.1"

  name             = local.name
  cidr             = "10.42.0.0/16"
  azs              = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets  = ["10.42.0.0/24", "10.42.1.0/24"]
  public_subnets   = ["10.42.16.0/24", "10.42.17.0/24"]
  database_subnets = ["10.42.32.0/24", "10.42.33.0/24"]

  enable_nat_gateway           = true
  single_nat_gateway           = true
  create_database_subnet_group = true
  enable_dns_hostnames         = true
  public_subnet_tags           = { "kubernetes.io/role/elb" = "1" }
  private_subnet_tags          = { "kubernetes.io/role/internal-elb" = "1" }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.26.0"

  name                                   = local.name
  kubernetes_version                     = "1.35"
  upgrade_policy                         = { support_type = "STANDARD" }
  endpoint_private_access                = true
  endpoint_public_access                 = true
  endpoint_public_access_cidrs           = [var.operator_cidr]
  vpc_id                                 = module.vpc.vpc_id
  subnet_ids                             = module.vpc.private_subnets
  control_plane_subnet_ids               = module.vpc.private_subnets
  authentication_mode                    = "API"
  enabled_log_types                      = ["api", "audit", "authenticator"]
  cloudwatch_log_group_retention_in_days = 7

  addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true, configuration_values = jsonencode({ enableNetworkPolicy = "true" }) }
    eks-pod-identity-agent = { before_compute = true }
    aws-ebs-csi-driver     = { pod_identity_association = [{ role_arn = aws_iam_role.ebs.arn, service_account = "ebs-csi-controller-sa" }] }
  }

  eks_managed_node_groups = {
    demo = {
      instance_types = ["m6i.large"]
      capacity_type  = "ON_DEMAND"
      min_size       = 1
      max_size       = 1
      desired_size   = 1
      subnet_ids     = [module.vpc.private_subnets[0]]
    }
  }

  access_entries = {
    bootstrap = {
      principal_arn = var.bootstrap_role_arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }
}

resource "aws_iam_role" "ebs" {
  name = "${local.name}-ebs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "pods.eks.amazonaws.com" }
      Action = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ebs" {
  role       = aws_iam_role.ebs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_security_group" "postgres" {
  name   = "${local.name}-postgres"
  vpc_id = module.vpc.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = aws_security_group.postgres.id
  referenced_security_group_id = module.eks.node_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_instance" "postgres" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "fleet"
  username                    = "fleet_admin"
  manage_master_user_password = true
  db_subnet_group_name        = module.vpc.database_subnet_group_name
  vpc_security_group_ids      = [aws_security_group.postgres.id]
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 1
  skip_final_snapshot         = true
}

resource "aws_ecr_repository" "image" {
  for_each             = local.prefixes
  name                 = "fleet-rehearsal/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_iam_role" "publisher" {
  name = "${local.name}-publisher"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Federated = var.github_oidc_provider_arn }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud"           = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub"           = "${local.github_prefix}:ref:refs/heads/main:workflow_ref:${local.github}/.github/workflows/publish-release.yml@refs/heads/main:event_name:workflow_run"
        "token.actions.githubusercontent.com:repository_id" = local.github_repo
        "token.actions.githubusercontent.com:ref"           = "refs/heads/main"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "publish" {
  role = aws_iam_role.publisher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
      "ecr:DescribeImages", "ecr:BatchGetImage"], Resource = [for repo in aws_ecr_repository.image : repo.arn] }
    ]
  })
}

resource "aws_budgets_budget" "staging" {
  name         = local.name
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = toset([60, 100])
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }
}

output "cluster_name" {
  value = module.eks.cluster_name
}
output "postgres_endpoint" {
  value = aws_db_instance.postgres.endpoint
}
output "postgres_bootstrap_secret_arn" {
  value = aws_db_instance.postgres.master_user_secret[0].secret_arn
}
output "publisher_role_arn" {
  value = aws_iam_role.publisher.arn
}
output "registry" {
  value = { for name, repo in aws_ecr_repository.image : name => repo.repository_url }
}
