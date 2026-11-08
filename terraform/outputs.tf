output "cluster_name" { value = module.eks.cluster_name }
output "app_role_arn" { value = module.app_irsa.iam_role_arn }
output "sns_topic_arn" { value = aws_sns_topic.notify.arn }
output "buckets" { value = { for k, b in aws_s3_bucket.data : k => b.bucket } }
output "db_write_endpoints" { value = { for k, d in aws_db_instance.primary : k => d.address } }
output "db_read_endpoints" { value = { for k, d in aws_db_instance.replica : k => d.address } }
