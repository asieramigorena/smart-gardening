# Secrets and settings the server and the devices read from SSM Parameter Store.
# SecureStrings are encrypted with the AWS managed key aws/ssm. Note: random_password and the
# IoT private keys are also kept in the Terraform state, which is why the state bucket is
# private, encrypted and TLS-only (see README.md). Accepted for the MVP.

# Database superuser password.

resource "random_password" "db" {
  length = 32
  # Only URL-safe characters, so the password can go in a postgres:// URL without escaping.
  special          = true
  override_special = "-_"
  min_lower        = 1
  min_upper        = 1
  min_numeric      = 1
}

resource "aws_ssm_parameter" "db_password" {
  name        = "/smart-gardening/db/password"
  description = "PostgreSQL password of the smart gardening database."
  type        = "SecureString"
  value       = random_password.db.result
}

# Passwords of the application roles. Same character set as the superuser password.
locals {
  generated_passwords = {
    db_app        = { name = "/smart-gardening/db/app_password", description = "PostgreSQL password of the application role (ingestion and decision services)." }
    db_grafana    = { name = "/smart-gardening/db/grafana_password", description = "PostgreSQL password of the read-only Grafana role." }
    grafana_admin = { name = "/smart-gardening/grafana/admin_password", description = "Password of the Grafana admin user." }
  }
}

resource "random_password" "generated" {
  for_each = local.generated_passwords

  length           = 32
  special          = true
  override_special = "-_"
  min_lower        = 1
  min_upper        = 1
  min_numeric      = 1
}

resource "aws_ssm_parameter" "generated_password" {
  for_each = local.generated_passwords

  name        = each.value.name
  description = each.value.description
  type        = "SecureString"
  value       = random_password.generated[each.key].result
}

# IoT device credentials. Copy them to the Pi once (see README.md); nothing reads them at runtime.
resource "aws_ssm_parameter" "iot_certificate_pem" {
  for_each = toset(var.devices)

  name        = "/smart-gardening/iot/${each.key}/certificate_pem"
  description = "X.509 certificate of IoT device ${each.key}."
  type        = "SecureString"
  value       = aws_iot_certificate.device[each.key].certificate_pem
}

resource "aws_ssm_parameter" "iot_private_key" {
  for_each = toset(var.devices)

  name        = "/smart-gardening/iot/${each.key}/private_key"
  description = "Private key of the certificate of IoT device ${each.key}."
  type        = "SecureString"
  value       = aws_iot_certificate.device[each.key].private_key
}

# Plain settings for the server and the devices.
resource "aws_ssm_parameter" "iot_endpoint" {
  name        = "/smart-gardening/iot/endpoint"
  description = "AWS IoT Core data endpoint (ATS) for MQTT and iot-data Publish."
  type        = "String"
  value       = data.aws_iot_endpoint.data.endpoint_address
}

resource "aws_ssm_parameter" "ingest_queue_url" {
  name        = "/smart-gardening/ingest/queue_url"
  description = "URL of the ingestion SQS queue."
  type        = "String"
  value       = aws_sqs_queue.ingest.url
}

resource "aws_ssm_parameter" "ingest_dlq_url" {
  name        = "/smart-gardening/ingest/dlq_url"
  description = "URL of the ingestion dead-letter queue."
  type        = "String"
  value       = aws_sqs_queue.ingest_dlq.url
}
