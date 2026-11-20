output "cluster_name" { value = module.eks.cluster_name }
output "app_role_arn" { value = module.app_irsa.iam_role_arn }
output "sns_topic_arn" { value = aws_sns_topic.notify.arn }
output "buckets" { value = { for k, b in aws_s3_bucket.data : k => b.bucket } }
output "db_write_endpoints" { value = { for k, d in aws_db_instance.primary : k => d.address } }
output "db_read_endpoints" { value = { for k, d in aws_db_instance.replica : k => d.address } }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.main.id }
output "cognito_client_id"    { value = aws_cognito_user_pool_client.web.id }
output "app_url" { value = local.edge_enabled ? "https://${local.fqdn}" : "" }
output "cloudfront_domain" { value = local.edge_enabled ? aws_cloudfront_distribution.hms[0].domain_name : "" }
output "ingress_lb_dns" { value = local.origin }
output "bastion_public_ip" { value = var.bastion_enabled ? aws_eip.bastion[0].public_ip : "" }
output "bastion_instance_id" { value = var.bastion_enabled ? aws_instance.bastion[0].id : "" }
output "bastion_ssm_command" {
  value = var.bastion_enabled ? "aws ssm start-session --target ${aws_instance.bastion[0].id} --region ${var.region}" : ""
}
