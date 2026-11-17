# Database password. Generated once and stored as an SSM SecureString (encrypted with the AWS
# managed key aws/ssm). Note: random_password also keeps the value in the Terraform state,
# which is why the state bucket is private, encrypted and TLS-only (see README.md).

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
