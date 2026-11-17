# Network: the default VPC, on purpose.
# For the MVP a single server is enough, so we skip a custom VPC, private subnets and NAT
# (a NAT gateway alone would cost more than the server). The instance gets a public IP only
# to reach AWS APIs (SSM, S3) and package mirrors; its security group allows NO inbound
# traffic, so nothing on the Internet can open a connection to it.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "server" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

resource "aws_security_group" "server" {
  name        = "smart-gardening-server"
  description = "Smart gardening server: no inbound rules (access via SSM), all outbound."
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name = "smart-gardening-server"
  }
}

# Outbound only: SSM agent, S3, Docker Hub and OS updates. There is deliberately no ingress rule.
resource "aws_vpc_security_group_egress_rule" "server_all_ipv4" {
  security_group_id = aws_security_group.server.id
  description       = "All outbound IPv4 traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
