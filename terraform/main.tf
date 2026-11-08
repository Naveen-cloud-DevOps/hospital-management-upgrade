terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
backend "s3" {} # bucket/key/region passed by the GitHub Action (-backend-config)
}

provider "aws" { region = var.region }

data "aws_caller_identity" "me" {}
data "aws_availability_zones" "az" { state = "available" }

# ---------------- network + EKS ----------------
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name               = "${var.project}-vpc"
  cidr               = var.vpc_cidr
  azs                = slice(data.aws_availability_zones.az.names, 0, var.az_count)
  private_subnets    = var.private_subnet_cidrs
  public_subnets     = var.public_subnet_cidrs
  enable_nat_gateway = true
  single_nat_gateway = var.single_nat_gateway

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name                             = var.cluster_name
  cluster_version                          = var.eks_version
  vpc_id                                   = module.vpc.vpc_id
  subnet_ids                               = module.vpc.private_subnets
  cluster_endpoint_public_access           = var.cluster_endpoint_public_access
  enable_irsa                              = true
  enable_cluster_creator_admin_permissions = true

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
    }
  }
}

# ---------------- separate S3 buckets per data type ----------------
locals { buckets = toset(var.data_buckets) }

resource "aws_s3_bucket" "data" {
  for_each = local.buckets
  bucket   = "${var.project}-${data.aws_caller_identity.me.account_id}-${each.key}"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each                = aws_s3_bucket.data
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------- SNS ----------------
resource "aws_sns_topic" "notify" {
  name = "${var.project}-notifications"

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------- IAM for pods (IRSA). No s3:DeleteObject on purpose ----------------
data "aws_iam_policy_document" "app" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = [for b in aws_s3_bucket.data : "${b.arn}/*"]
  }
  statement {
    actions   = ["sns:Publish", "sns:Subscribe"]
    resources = [aws_sns_topic.notify.arn]
  }
}

resource "aws_iam_policy" "app" {
  name   = "${var.project}-app"
  policy = data.aws_iam_policy_document.app.json

  lifecycle {
    create_before_destroy = true
  }
}

module "app_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name         = "${var.project}-app"
  role_policy_arns  = { app = aws_iam_policy.app.arn }
  oidc_providers = {
    main = { provider_arn = module.eks.oidc_provider_arn, namespace_service_accounts = ["${var.k8s_namespace}:${var.k8s_service_account}"] }
  }
}
