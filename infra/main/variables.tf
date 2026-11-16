variable "aws_region" {
  description = "AWS region for every resource of the project."
  type        = string
  default     = "eu-south-2"
}

variable "availability_zone" {
  description = "AZ of the server. Changing it replaces the instance (and its disk), so pick once."
  type        = string
  default     = "eu-south-2a"
}

variable "instance_type" {
  description = "EC2 instance type of the server. Must be x86_64 (the AMI and the compose plugin are x86_64)."
  type        = string
  default     = "t3.small"
}
