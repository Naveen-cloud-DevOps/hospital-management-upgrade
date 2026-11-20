
# ---------------------------------------------------------------------------
# dns.tf
# ingress-nginx Load Balancer -> CloudFront -> Route53
#
# ACM certificate is created in us-east-1.
# ACM DNS validation records are created in Route53.
# CloudFront uses the validated ACM certificate.
#
# Required variables:
# hosted_zone_name
# app_subdomain
# origin_dns
# cloudfront_price_class
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AWS Provider for CloudFront ACM Certificate
# ---------------------------------------------------------------------------

provider "aws" {
  alias  = "use1"
  region = "us-east-1"
}


# ---------------------------------------------------------------------------
# Discover ingress-nginx Load Balancer
# ---------------------------------------------------------------------------

data "aws_resourcegroupstaggingapi_resources" "ingress_lb" {
  resource_type_filters = [
    "elasticloadbalancing:loadbalancer"
  ]

  tag_filter {
    key = "kubernetes.io/service-name"

    values = [
      "ingress-nginx/ingress-nginx-controller"
    ]
  }
}


# ---------------------------------------------------------------------------
# Load Balancer Locals
# ---------------------------------------------------------------------------

locals {
  lb_arn = try(
    data.aws_resourcegroupstaggingapi_resources.ingress_lb.resource_tag_mapping_list[0].resource_arn,
    ""
  )

  lb_is_v2 = can(
    regex("loadbalancer/(net|app)/", local.lb_arn)
  )

  lb_name = local.lb_arn == "" ? "" : element(
    split("/", local.lb_arn),
    length(split("/", local.lb_arn)) - 1
  )

  use_classic = local.lb_arn != "" && !local.lb_is_v2

  use_v2 = local.lb_arn != "" && local.lb_is_v2
}


# ---------------------------------------------------------------------------
# Classic ELB
# ---------------------------------------------------------------------------

data "aws_elb" "ingress" {
  count = local.use_classic ? 1 : 0

  name = local.lb_name
}


# ---------------------------------------------------------------------------
# Application / Network Load Balancer
# ---------------------------------------------------------------------------

data "aws_lb" "ingress" {
  count = local.use_v2 ? 1 : 0

  arn = local.lb_arn
}


# ---------------------------------------------------------------------------
# Origin and Edge Configuration
# ---------------------------------------------------------------------------

locals {
  discovered_origin = local.use_classic ? (
    data.aws_elb.ingress[0].dns_name
  ) : (
    local.use_v2 ? data.aws_lb.ingress[0].dns_name : ""
  )

  origin = var.origin_dns != "" ? (
    var.origin_dns
  ) : (
    local.discovered_origin
  )

  edge_enabled = (
    var.hosted_zone_name != "" &&
    local.origin != ""
  )

  fqdn = var.app_subdomain == "" ? (
    var.hosted_zone_name
  ) : (
    "${var.app_subdomain}.${var.hosted_zone_name}"
  )
}


# ---------------------------------------------------------------------------
# Route53 Hosted Zone
# ---------------------------------------------------------------------------

data "aws_route53_zone" "this" {
  count = var.hosted_zone_name != "" ? 1 : 0

  name         = var.hosted_zone_name
  private_zone = false
}


# ---------------------------------------------------------------------------
# ACM Certificate
# ---------------------------------------------------------------------------

resource "aws_acm_certificate" "cf" {
  count = local.edge_enabled ? 1 : 0

  provider = aws.use1

  domain_name       = local.fqdn
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}


# ---------------------------------------------------------------------------
# ACM DNS Validation Records
#
# IMPORTANT:
# Use ACM domain_validation_options as the for_each source.
# Do not use one(), tolist()[0], or a manually selected record.
# ---------------------------------------------------------------------------

resource "aws_route53_record" "cert_validation" {
  for_each = local.edge_enabled ? {
    for dvo in aws_acm_certificate.cf[0].domain_validation_options :
    dvo.domain_name => dvo
  } : {}

  zone_id = data.aws_route53_zone.this[0].zone_id

  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]

  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "cf" {
  count                   = local.edge_enabled ? 1 : 0
  provider                = aws.use1
  certificate_arn         = aws_acm_certificate.cf[0].arn
  validation_record_fqdns = [
    for r in aws_route53_record.cert_validation : r.fqdn
  ]
}

# ---------------------------------------------------------------------------
# CloudFront Cache Policy
# ---------------------------------------------------------------------------

data "aws_cloudfront_cache_policy" "no_cache" {
  name = "Managed-CachingDisabled"
}


# ---------------------------------------------------------------------------
# CloudFront Origin Request Policy
# ---------------------------------------------------------------------------

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}


# ---------------------------------------------------------------------------
# CloudFront Distribution
# ---------------------------------------------------------------------------

resource "aws_cloudfront_distribution" "hms" {
  count = local.edge_enabled ? 1 : 0

  enabled = true

  comment = "hms ingress-nginx load balancer"

  aliases = [
    local.fqdn
  ]

  price_class = var.cloudfront_price_class

  is_ipv6_enabled = true


  # -------------------------------------------------------------------------
  # Origin
  # -------------------------------------------------------------------------

  origin {
    origin_id = "ingress-nginx-lb"

    domain_name = local.origin

    custom_origin_config {
      http_port = 80

      https_port = 443

      origin_protocol_policy = "http-only"

      origin_ssl_protocols = [
        "TLSv1.2"
      ]

      origin_read_timeout = 60
    }
  }


  # -------------------------------------------------------------------------
  # Default Cache Behavior
  # -------------------------------------------------------------------------

  default_cache_behavior {
    target_origin_id = "ingress-nginx-lb"

    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = [
      "GET",
      "HEAD",
      "OPTIONS",
      "PUT",
      "POST",
      "PATCH",
      "DELETE"
    ]

    cached_methods = [
      "GET",
      "HEAD"
    ]

    compress = true

    cache_policy_id = data.aws_cloudfront_cache_policy.no_cache.id

    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }


  # -------------------------------------------------------------------------
  # Restrictions
  # -------------------------------------------------------------------------

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }


  # -------------------------------------------------------------------------
  # Viewer Certificate
  # -------------------------------------------------------------------------

  viewer_certificate {
    acm_certificate_arn = aws_acm_certificate_validation.cf[0].certificate_arn

    ssl_support_method = "sni-only"

    minimum_protocol_version = "TLSv1.2_2021"
  }
}


# ---------------------------------------------------------------------------
# Route53 A Record -> CloudFront
# ---------------------------------------------------------------------------

resource "aws_route53_record" "app_a" {
  count = local.edge_enabled ? 1 : 0

  zone_id = data.aws_route53_zone.this[0].zone_id

  name = local.fqdn

  type = "A"

  alias {
    name = aws_cloudfront_distribution.hms[0].domain_name

    zone_id = aws_cloudfront_distribution.hms[0].hosted_zone_id

    evaluate_target_health = false
  }
}


# ---------------------------------------------------------------------------
# Route53 AAAA Record -> CloudFront
# ---------------------------------------------------------------------------

resource "aws_route53_record" "app_aaaa" {
  count = local.edge_enabled ? 1 : 0

  zone_id = data.aws_route53_zone.this[0].zone_id

  name = local.fqdn

  type = "AAAA"

  alias {
    name = aws_cloudfront_distribution.hms[0].domain_name

    zone_id = aws_cloudfront_distribution.hms[0].hosted_zone_id

    evaluate_target_health = false
  }
}
