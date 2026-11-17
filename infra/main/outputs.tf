output "instance_id" {
  description = "ID of the server instance (SSM target)."
  value       = aws_instance.server.id
}

output "tfstate_bucket" {
  description = "S3 bucket with the Terraform state (created by infra/bootstrap)."
  value       = "smart-gardening-tfstate-${data.aws_caller_identity.current.account_id}"
}

output "backups_bucket" {
  description = "S3 bucket for database dumps (objects expire after 14 days)."
  value       = aws_s3_bucket.backups.bucket
}

output "db_password_parameter" {
  description = "SSM parameter (SecureString) with the database password."
  value       = aws_ssm_parameter.db_password.name
}

output "ssm_shell_command" {
  description = "Open a shell on the server."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.server.id}"
}

output "ssm_postgres_tunnel_command" {
  description = "Forward localhost:5432 on your laptop to port 5432 on the server (keep it running)."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.server.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"5432\"],\"localPortNumber\":[\"5432\"]}'"
}

output "db_password_command" {
  description = "Print the database password."
  value       = "aws ssm get-parameter --region ${var.aws_region} --name ${aws_ssm_parameter.db_password.name} --with-decryption --query Parameter.Value --output text"
}
