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

output "ingest_queue_url" {
  description = "URL of the ingestion SQS queue (also in SSM /smart-gardening/ingest/queue_url)."
  value       = aws_sqs_queue.ingest.url
}

output "ingest_dlq_url" {
  description = "URL of the ingestion dead-letter queue (also in SSM /smart-gardening/ingest/dlq_url)."
  value       = aws_sqs_queue.ingest_dlq.url
}

output "iot_topic_rule" {
  description = "Name of the IoT topic rule that forwards readings and events to SQS."
  value       = aws_iot_topic_rule.ingest.name
}

output "iot_endpoint" {
  description = "AWS IoT Core data endpoint (ATS) for MQTT and iot-data Publish (also in SSM /smart-gardening/iot/endpoint)."
  value       = data.aws_iot_endpoint.data.endpoint_address
}

output "iot_device_credentials_commands" {
  description = "Per device: commands that save its certificate and private key to the current directory."
  value = {
    for d in var.devices : d => {
      certificate = "aws ssm get-parameter --region ${var.aws_region} --name ${aws_ssm_parameter.iot_certificate_pem[d].name} --with-decryption --query Parameter.Value --output text > ${d}.cert.pem"
      private_key = "(umask 077; aws ssm get-parameter --region ${var.aws_region} --name ${aws_ssm_parameter.iot_private_key[d].name} --with-decryption --query Parameter.Value --output text > ${d}.private.key)"
    }
  }
}
