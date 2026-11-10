# ---------------------------------------------------------------------------
# dns.tf  -  ingress-nginx load balancer  ->  CloudFront  ->  Route53
#
# Lives in the same Terraform folder/state as main.tf (no existing file is edited).
# The load balancer is created later by ArgoCD (ingress-nginx), so it is discovered
# automatically by its Kubernetes tag. Until it exists (first terraform-infra run) and
# until hosted_zone_name is set, every resource below is skipped (count = 0).
# The "edge-cloudfront" workflow re-applies this folder after "cicd" finishes.
#
# Required: put   hosted_zone_name = "yourdomain.com"   in the TFVARS secret / terraform.tfvars
# (keep it there permanently, otherwise a later run would remove CloudFront).
# ---------------------------------------------------------------------------

variable "hosted_zone_name" {
  description = "Existing Route53 PUBLIC hosted zone, e.g. example.com. Empty = skip CloudFront/Route53"
  type        = string
  default     = ""
}

variable "app_subdomain" {
  description = "Record inside the zone, e.g. hms -> hms.example.com. Empty = zone apex"
  type        = string
  default     = "hms"
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

# CloudFront only accepts ACM certificates from us-east-1
provider "aws" {
  alias  = "use1"
  region = "us-east-1"
}

# ---------------- find the ingress-nginx load balancer ----------------
data "aws_resourcegroupstaggingapi_resources" "ingress_lb" {
  resource_type_filters = ["elasticloadbalancing:loadbalancer"]
  tag_filter {
    key    = "kubernetes.io/service-name"
    values = ["ingress-nginx/ingress-nginx-controller"]
  }
}

locals {
  lb_arn        = try(data.aws_resourcegroupstaggingapi_resources.ingress_lb.resource_tag_mapping_list[0].resource_arn, "")
  lb_is_v2      = can(regex("loadbalancer/(net|app)/", local.lb_arn))
  lb_name       = local.lb_arn == "" ? "" : element(split("/", local.lb_arn), length(split("/", local.lb_arn)) - 1)
  use_classic   = local.lb_arn != "" && !local.lb_is_v2
  use_v2        = local.lb_arn != "" && local.lb_is_v2
}

data "aws_elb" "ingress" {
  count = local.use_classic ? 1 : 0
  name  = local.lb_name
}

data "aws_lb" "ingress" {
  count = local.use_v2 ? 1 : 0
  arn   = local.lb_arn
}

locals {
  discovered_origin = local.use_classic ? data.aws_elb.ingress[0].dns_name : (local.use_v2 ? data.aws_lb.ingress[0].dns_name : "")
  origin            = var.origin_dns != "" ? var.origin_dns : local.discovered_origin
  edge_enabled      = var.hosted_zone_name != "" && local.origin != ""
  fqdn              = var.app_subdomain == "" ? var.hosted_zone_name : "${var.app_subdomain}.${var.hosted_zone_name}"
}

data "aws_route53_zone" "this" {
  count        = var.hosted_zone_name != "" ? 1 : 0
  name         = var.hosted_zone_name
  private_zone = false
}

# ---------------- ACM certificate (DNS validated in Route53) ----------------
resource "aws_acm_certificate" "cf" {
  count             = local.edge_enabled ? 1 : 0
  provider          = aws.use1
  domain_name       = local.fqdn
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.edge_enabled ? {
    for o in aws_acm_certificate.cf[0].domain_validation_options : o.domain_name => {
      name   = o.resource_record_name
      type   = o.resource_record_type
      record = o.resource_record_value
    }
  } : {}
  zone_id         = data.aws_route53_zone.this[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "cf" {
  count                   = local.edge_enabled ? 1 : 0
  provider                = aws.use1
  certificate_arn         = aws_acm_certificate.cf[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# ---------------- CloudFront ----------------
data "aws_cloudfront_cache_policy" "no_cache" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_distribution" "hms" {
  count           = local.edge_enabled ? 1 : 0
  enabled         = true
  comment         = "hms ingress-nginx load balancer"
  aliases         = [local.fqdn]
  price_class     = var.cloudfront_price_class
  is_ipv6_enabled = true

  origin {
    origin_id   = "ingress-nginx-lb"
    domain_name = local.origin

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # CloudFront -> load balancer on port 80
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }
  }

  default_cache_behavior {
    target_origin_id         = "ingress-nginx-lb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = data.aws_cloudfront_cache_policy.no_cache.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.cf[0].certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

# ---------------- Route53 -> CloudFront ----------------
resource "aws_route53_record" "app_a" {
  count   = local.edge_enabled ? 1 : 0
  zone_id = data.aws_route53_zone.this[0].zone_id
  name    = local.fqdn
  type    = "A"
  alias {
    name                   = aws_cloudfront_distribution.hms[0].domain_name
    zone_id                = aws_cloudfront_distribution.hms[0].hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "app_aaaa" {
  count   = local.edge_enabled ? 1 : 0
  zone_id = data.aws_route53_zone.this[0].zone_id
  name    = local.fqdn
  type    = "AAAA"
  alias {
    name                   = aws_cloudfront_distribution.hms[0].domain_name
    zone_id                = aws_cloudfront_distribution.hms[0].hosted_zone_id
    evaluate_target_health = false
  }
}

output "app_url" { value = local.edge_enabled ? "https://${local.fqdn}" : "" }
output "cloudfront_domain" { value = local.edge_enabled ? aws_cloudfront_distribution.hms[0].domain_name : "" }
output "ingress_lb_dns" { value = local.origin }
