# ---------------------------------------------------------------------------
# bastion.tf - public "client" server in the SAME VPC that can run kubectl/helm
# against the EKS cluster. Nothing in main.tf / rds.tf / dns.tf is edited. 
#
# Access:  AWS Systems Manager Session Manager (no inbound port needed)
#          + optional SSH, only from the CIDRs you list in bastion_allowed_cidrs.
# ---------------------------------------------------------------------------
# ---------------- AMI (Amazon Linux 2023, latest) ----------------
data "aws_ssm_parameter" "al2023" {
  count = var.bastion_enabled ? 1 : 0
  name  = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ---------------- security group ----------------
resource "aws_security_group" "bastion" {
  count       = var.bastion_enabled ? 1 : 0
  name        = "${var.project}-bastion"
  description = "Public client server"
  vpc_id      = module.vpc.vpc_id

  dynamic "ingress" {
    for_each = length(var.bastion_allowed_cidrs) > 0 ? [1] : []
    content {
      description = "SSH from allowed IPs only"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.bastion_allowed_cidrs
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-bastion" }
}

# EKS API (private endpoint inside the VPC) must accept the bastion on 443
resource "aws_security_group_rule" "eks_api_from_bastion" {
  count                    = var.bastion_enabled ? 1 : 0
  type                     = "ingress"
  description              = "kubectl from bastion"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = module.eks.cluster_security_group_id
  source_security_group_id = aws_security_group.bastion[0].id
}

# ---------------- IAM role for the server ----------------
data "aws_iam_policy_document" "bastion_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "bastion" {
  count              = var.bastion_enabled ? 1 : 0
  name               = "${var.project}-bastion"
  assume_role_policy = data.aws_iam_policy_document.bastion_assume.json
}

resource "aws_iam_role_policy_attachment" "bastion_ssm" {
  count      = var.bastion_enabled ? 1 : 0
  role       = aws_iam_role.bastion[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "bastion_eks" {
  count = var.bastion_enabled ? 1 : 0
  name  = "eks-describe"
  role  = aws_iam_role.bastion[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["eks:DescribeCluster", "eks:ListClusters"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "bastion" {
  count = var.bastion_enabled ? 1 : 0
  name  = "${var.project}-bastion"
  role  = aws_iam_role.bastion[0].name
}

# ---------------- let that role into the cluster (Kubernetes RBAC) ----------------
resource "aws_eks_access_entry" "bastion" {
  count         = var.bastion_enabled ? 1 : 0
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.bastion[0].arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "bastion" {
  count         = var.bastion_enabled ? 1 : 0
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.bastion[0].arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.bastion]
}

# ---------------- the server (public subnet) ----------------
resource "aws_instance" "bastion" {
  count                       = var.bastion_enabled ? 1 : 0
  ami                         = data.aws_ssm_parameter.al2023[0].value
  instance_type               = var.bastion_instance_type
  subnet_id                   = module.vpc.public_subnets[0]
  vpc_security_group_ids      = [aws_security_group.bastion[0].id]
  iam_instance_profile        = aws_iam_instance_profile.bastion[0].name
  #key_name                    = var.bastion_key_name != "" ? var.bastion_key_name : null
  associate_public_ip_address = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = <<-EOT
    #!/bin/bash
    set -e
    dnf install -y git jq
    KVER=$(curl -sL https://dl.k8s.io/release/stable-${var.eks_version}.txt)
    curl -sLo /usr/local/bin/kubectl https://dl.k8s.io/release/$KVER/bin/linux/amd64/kubectl
    chmod +x /usr/local/bin/kubectl
    curl -s https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    sudo -u ec2-user -H aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}
  EOT

  tags = { Name = "${var.project}-bastion" }

  lifecycle {
    ignore_changes = [ami] # do not rebuild the server when a newer AMI is published
  }

  depends_on = [aws_eks_access_policy_association.bastion]
}

# stable public IP (survives stop/start)
resource "aws_eip" "bastion" {
  count    = var.bastion_enabled ? 1 : 0
  instance = aws_instance.bastion[0].id
  domain   = "vpc"
  tags     = { Name = "${var.project}-bastion" }
}

