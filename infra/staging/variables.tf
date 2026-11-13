variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Use the approved sandbox account ID."
  }
}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "owner" {
  type = string
}
variable "expires_at" {
  type = string
  validation {
    condition     = can(formatdate("YYYY-MM-DD", var.expires_at))
    error_message = "Use an RFC3339 teardown time."
  }
}
variable "operator_cidr" {
  type = string
  validation {
    condition     = can(cidrnetmask(var.operator_cidr)) && try(tonumber(split("/", var.operator_cidr)[1]) >= 24, false)
    error_message = "Use an approved IPv4 CIDR of /24 or narrower for the EKS control plane."
  }
}
variable "bootstrap_role_arn" {
  type = string
}
variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN in the sandbox account."
  type        = string
}
variable "budget_email" {
  type = string
}
variable "monthly_budget_usd" {
  type    = number
  default = 25
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "Supply a positive budget approved by the account owner."
  }
}
