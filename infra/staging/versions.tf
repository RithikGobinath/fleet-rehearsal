terraform {
  required_version = "~> 1.14.0"
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.59.0"
    }
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      Project   = "fleet-rehearsal"
      Stage     = "staging"
      Owner     = var.owner
      ExpiresAt = var.expires_at
      ManagedBy = "terraform"
    }
  }
}
