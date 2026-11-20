
# ---------------- general ----------------

variable "region" {
  description = "AWS region for all resources"
  type        = string
}

variable "project" {
  description = "Prefix used in resource names"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

# ---------------- network ----------------

variable "vpc_cidr" {
  description = "CIDR block of the VPC"
  type        = string
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across (must match subnet list lengths)"
  type        = number
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs (EKS nodes, RDS), one per AZ"
  type        = list(string)
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs (load balancers, NAT), one per AZ"
  type        = list(string)
}

variable "single_nat_gateway" {
  description = "true = one shared NAT gateway (cheaper), false = one per AZ (highly available)"
  type        = bool
}

# ---------------- EKS ----------------

variable "eks_version" {
  description = "Kubernetes version of the EKS cluster"
  type        = string
}

variable "node_instance_types" {
  description = "EC2 instance types for the managed node group"
  type        = list(string)
}

variable "node_min_size" {
  type = number
}

variable "node_max_size" {
  type = number
}

variable "node_desired_size" {
  type = number
}

variable "cluster_endpoint_public_access" {
  description = "Expose the Kubernetes API publicly (GitHub runners need this to deploy)"
  type        = bool
}

# ---------------- S3 / IRSA ----------------

variable "data_buckets" {
  description = "Separate S3 buckets, one per data type"
  type        = list(string)
}

variable "k8s_namespace" {
  description = "Kubernetes namespace of the app"
  type        = string
}

variable "k8s_service_account" {
  description = "Kubernetes service account that assumes the app IAM role"
  type        = string
}

# ---------------- RDS ----------------

variable "db_services" {
  description = "One PostgreSQL instance (+ read replica) per service"
  type        = list(string)
}

variable "db_engine_version" {
  type = string
}

variable "db_instance_class" {
  description = "Instance class for primaries"
  type        = string
}

variable "db_replica_instance_class" {
  description = "Instance class for read replicas"
  type        = string
}

variable "db_allocated_storage" {
  description = "Storage in GiB for each primary"
  type        = number
}

variable "db_storage_type" {
  type = string
}

variable "db_backup_retention_days" {
  description = "Must be >= 1 for read replicas"
  type        = number

  validation {
    condition     = var.db_backup_retention_days >= 1
    error_message = "Read replicas require backup retention of at least 1 day."
  }
}

variable "db_deletion_protection" {
  type = bool
}

# ---------------- secrets (never put real values in git) ----------------

variable "db_username" {
  description = "Master username (GitHub secret DB_USERNAME in CI, terraform.tfvars locally)"
  type        = string
  sensitive   = true
}

variable "db_password" {
  description = "Master password (GitHub secret DB_PASSWORD in CI, terraform.tfvars locally)"
  type        = string
  sensitive   = true
}

#---------------------------------Route53&Cloudfront-----------------------------------#

variable "hosted_zone_name" {
  description = "Existing Route53 PUBLIC hosted zone, e.g. example.com. Empty = skip CloudFront/Route53"
  type        = string
  default     = ""
}

variable "app_subdomain" {
  description = "Record inside the zone, e.g. hms -> hms.example.com. Empty = zone apex"
  type        = string
  default     = ""
}

variable "origin_dns" {
  description = "Optional manual override of the load balancer DNS name. Empty = auto-discover"
  type        = string
  default     = ""
}

variable "cloudfront_price_class" {
  type    = string
  default = "PriceClass_100"
}

#----------------------bastion--------------------------------------#


variable "bastion_enabled" {
  description = "Create the public client server"
  type        = bool
  default     = true
}

variable "bastion_instance_type" {
  type    = string
  default = "t3.micro"
}

variable "bastion_allowed_cidrs" {
  description = "CIDRs allowed to SSH, e.g. [\"203.0.113.10/32\"] (your public IP). Empty = no SSH, use SSM only. Never use 0.0.0.0/0."
  type        = list(string)
  default     = []
}

variable "bastion_key_name" {
  description = "Existing EC2 key pair name for SSH. Empty = no key (SSM only)"
  type        = string
  default     = ""
}
