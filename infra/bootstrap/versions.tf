terraform {
  required_version = "~> 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # The state of this module lives in the bucket it creates. The very first apply runs with
  # local state (see README.md, "Bootstrap") and is then migrated here with -migrate-state.
  # The bucket name contains the account ID, so it comes from ../backend.hcl (partial config).
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "eu-south-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "smart-gardening"
      ManagedBy = "terraform"
    }
  }
}
