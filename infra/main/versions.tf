terraform {
  required_version = "~> 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Created by infra/bootstrap. The bucket name contains the account ID, so it comes from
  # ../backend.hcl (git-ignored): terraform init -backend-config=../backend.hcl
  backend "s3" {
    key          = "main/terraform.tfstate"
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
