# ---------------- general ----------------
# NOTE: the docker-build workflow and k8s manifests currently assume
# region=us-east-1, project=hms, cluster_name=hms-eks. If you change these three,
# update those files too.
variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Prefix used in resource names"
  type        = string
  default     = "hms"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "hms-eks"
}

# ---------------- network ----------------
variable "vpc_cidr" {
  description = "CIDR block of the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across (must match subnet list lengths)"
  type        = number
  default     = 2
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs (EKS nodes, RDS), one per AZ"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs (load balancers, NAT), one per AZ"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24"]
}

variable "single_nat_gateway" {
  description = "true = one shared NAT gateway (cheaper), false = one per AZ (highly available)"
  type        = bool
  default     = true
}

# ---------------- EKS ----------------
variable "eks_version" {
  description = "Kubernetes version of the EKS cluster"
  type        = string
  default     = "1.35"
}

variable "ami_type" {
  description = "EKS worker node AMI type"
  type        = string
  default     = "AL2023_x86_64_STANDARD"
}

variable "node_instance_types" {
  description = "EC2 instance types for the managed node group"
  type        = list(string)
  default     = ["t3.large"]
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "node_desired_size" {
  type    = number
  default = 3
}

variable "cluster_endpoint_public_access" {
  description = "Expose the Kubernetes API publicly (GitHub runners need this to deploy)"
  type        = bool
  default     = true
}

# ---------------- S3 / IRSA ----------------
variable "data_buckets" {
  description = "Separate S3 buckets, one per data type"
  type        = list(string)
  default     = ["user-data", "payment-receipts", "patient-photos", "appointments", "lab-reports", "pharmacy-bills"]
}

variable "k8s_namespace" {
  description = "Kubernetes namespace of the app"
  type        = string
  default     = "hms"
}

variable "k8s_service_account" {
  description = "Kubernetes service account that assumes the app IAM role"
  type        = string
  default     = "hms-sa"
}

# ---------------- RDS ----------------
variable "db_services" {
  description = "One PostgreSQL instance (+ read replica) per service"
  type        = list(string)
  default     = ["auth", "patient", "billing", "appointment", "doctor", "laboratory", "pharmacy"]
}

variable "db_engine_version" {
  type    = string
  default = "16"
}

variable "db_instance_class" {
  description = "Instance class for primaries"
  type        = string
  default     = "db.t3.micro"
}

variable "db_replica_instance_class" {
  description = "Instance class for read replicas"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "Storage in GiB for each primary"
  type        = number
  default     = 20
}

variable "db_storage_type" {
  type    = string
  default = "gp3"
}

variable "db_backup_retention_days" {
  description = "Must be >= 1 for read replicas"
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 1
    error_message = "Read replicas require backup retention of at least 1 day."
  }
}

variable "db_deletion_protection" {
  type    = bool
  default = true
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
