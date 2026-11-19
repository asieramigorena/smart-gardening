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

variable "devices" {
  description = "IoT thing names of the devices (one Raspberry Pi each). Each gets a certificate; also used as MQTT client ID and in the topics."
  type        = list(string)
  default     = ["pi-01"]

  validation {
    condition     = alltrue([for d in var.devices : can(regex("^[A-Za-z0-9_-]{1,64}$", d))])
    error_message = "Device names must match ^[A-Za-z0-9_-]{1,64}$ (see docs/contracts/README.md)."
  }
}
