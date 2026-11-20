# ===========================================================================
# GENERAL
# ===========================================================================

variable "region" {
  description = "AWS deployment region"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}


# ===========================================================================
# NETWORK
# ===========================================================================

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
}

variable "az_count" {
  description = "Number of availability zones"
  type        = number

  validation {
    condition     = var.az_count >= 2
    error_message = "At least two availability zones are required."
  }
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDR blocks"
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_cidrs) == var.az_count
    error_message = "Private subnet count must match az_count."
  }
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDR blocks"
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_cidrs) == var.az_count
    error_message = "Public subnet count must match az_count."
  }
}

variable "single_nat_gateway" {
  description = "Use a single NAT gateway"
  type        = bool
}


# ===========================================================================
# EKS
# ===========================================================================

variable "eks_version" {
  description = "EKS Kubernetes version"
  type        = string
}

variable "node_instance_types" {
  description = "EKS worker node instance types"
  type        = list(string)
}

variable "node_min_size" {
  description = "Minimum node count"
  type        = number
}

variable "node_max_size" {
  description = "Maximum node count"
  type        = number
}

variable "node_desired_size" {
  description = "Desired node count"
  type        = number

  validation {
    condition = (
      var.node_desired_size >= var.node_min_size &&
      var.node_desired_size <= var.node_max_size
    )
    error_message = "Desired node count must be between minimum and maximum."
  }
}

variable "cluster_endpoint_public_access" {
  description = "Enable public EKS endpoint"
  type        = bool
}


# ===========================================================================
# S3 / IRSA
# ===========================================================================

variable "data_buckets" {
  description = "S3 application data buckets"
  type        = list(string)
}

variable "k8s_namespace" {
  description = "Kubernetes namespace"
  type        = string
}

variable "k8s_service_account" {
  description = "Kubernetes service account"
  type        = string
}


# ===========================================================================
# RDS
# ===========================================================================

variable "db_services" {
  description = "Database service names"
  type        = list(string)
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
}

variable "db_instance_class" {
  description = "Primary RDS instance class"
  type        = string
}

variable "db_replica_instance_class" {
  description = "RDS replica instance class"
  type        = string
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GiB"
  type        = number
}

variable "db_storage_type" {
  description = "RDS storage type"
  type        = string
}

variable "db_backup_retention_days" {
  description = "RDS backup retention days"
  type        = number

  validation {
    condition = (
      var.db_backup_retention_days >= 1 &&
      var.db_backup_retention_days <= 35
    )
    error_message = "Backup retention must be between 1 and 35 days."
  }
}

variable "db_deletion_protection" {
  description = "Enable RDS deletion protection"
  type        = bool
}

variable "db_username" {
  description = "RDS master username"
  type        = string
  sensitive   = true
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}


# ===========================================================================
# ROUTE53 / CLOUDFRONT
# ===========================================================================

variable "hosted_zone_name" {
  description = "Route53 public hosted zone"
  type        = string
  default     = ""
}

variable "app_subdomain" {
  description = "Application subdomain"
  type        = string
  default     = ""
}

variable "origin_dns" {
  description = "Optional load balancer DNS override"
  type        = string
  default     = ""
}

variable "cloudfront_price_class" {
  description = "CloudFront price class"
  type        = string
  default     = "PriceClass_100"

  validation {
    condition = contains(
      ["PriceClass_100", "PriceClass_200", "PriceClass_All"],
      var.cloudfront_price_class
    )
    error_message = "Invalid CloudFront price class."
  }
}


# ===========================================================================
# BASTION
# ===========================================================================

variable "bastion_enabled" {
  description = "Enable bastion host"
  type        = bool
  default     = true
}

variable "bastion_instance_type" {
  description = "Bastion EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "bastion_allowed_cidrs" {
  description = "Allowed SSH source CIDRs"
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.bastion_allowed_cidrs :
      can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"
    ])
    error_message = "Provide valid CIDRs and do not allow SSH from 0.0.0.0/0."
  }
}

variable "bastion_key_name" {
  description = "Existing EC2 key pair name"
  type        = string
  default     = ""
}
