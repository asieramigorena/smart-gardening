output "tfstate_bucket" {
  description = "Name of the S3 bucket that stores the Terraform state."
  value       = aws_s3_bucket.tfstate.bucket
}

output "backend_hcl" {
  description = "Content for infra/backend.hcl (git-ignored): terraform output -raw backend_hcl > ../backend.hcl"
  value       = "bucket = \"${aws_s3_bucket.tfstate.bucket}\"\n"
}
