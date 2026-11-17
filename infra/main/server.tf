# The server: one EC2 instance that runs PostgreSQL and the API in Docker.
# The database lives on its root volume, so the instance must never be replaced by accident.

# Latest Amazon Linux 2023 (x86_64). An aws_ami lookup is used instead of the public SSM
# parameter /aws/service/ami-amazon-linux-latest/..., which our IAM permissions do not allow.
# AWS publishes one AMI per kernel line on the same day, so the kernel line is pinned to make
# the choice deterministic (6.18 is the newest LTS line). It only matters at creation time.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-6.18-x86_64"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "server" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.server.id
  vpc_security_group_ids      = [aws_security_group.server.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2.name
  associate_public_ip_address = true # outbound Internet access in the default VPC (no NAT)

  # No key_name: there is no SSH. Shell and port forwarding go through SSM Session Manager.

  user_data = file("${path.module}/user_data.sh")

  # Burstable instances default to "unlimited" CPU credits, which can add charges.
  # "standard" caps usage at the baseline instead (we have no AWS Budgets alerts).
  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
    # 2 hops so containers (e.g. a backup job using the instance role) can reach IMDSv2.
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true # AWS managed key aws/ebs
  }

  tags = {
    Name = "smart-gardening-server"
    # Also set by default_tags. Kept explicit because SSM Session Manager access is granted
    # by this tag: without it nobody can open a session.
    Project = "smart-gardening"
  }

  lifecycle {
    # A new AMI or a user_data edit would otherwise replace the instance and lose the database.
    # Update the OS in place (dnf upgrade) instead.
    ignore_changes = [ami, user_data]
  }
}
