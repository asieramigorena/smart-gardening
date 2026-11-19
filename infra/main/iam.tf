# Instance role: SSM Session Manager, read the project's parameters, read/write the backups bucket,
# consume the ingestion queue and publish irrigation commands to the devices.

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "smart-gardening-ec2"
  description        = "Smart gardening server: SSM access, project parameters and DB backups."
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "smart-gardening-ec2"
  role = aws_iam_role.ec2.name
}

locals {
  ssm_parameter_arn_prefix = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter"
}

data "aws_iam_policy_document" "ec2" {
  statement {
    sid = "ReadProjectParameters"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [
      "${local.ssm_parameter_arn_prefix}/smart-gardening",
      "${local.ssm_parameter_arn_prefix}/smart-gardening/*",
    ]
  }

  # SecureString parameters use the AWS managed key aws/ssm. Its ARN cannot be looked up
  # without KMS permissions, so the resource is "*" and the condition limits Decrypt to
  # calls made by SSM in this region.
  statement {
    sid       = "DecryptParametersViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "ListBackupsBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.backups.arn]
  }

  statement {
    sid = "ReadWriteBackups"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.backups.arn}/*"]
  }

  # The server has no business with the devices' private keys, even though they live under
  # /smart-gardening/*. An explicit Deny wins over the Allow above.
  statement {
    sid       = "DenyDevicePrivateKeys"
    effect    = "Deny"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["${local.ssm_parameter_arn_prefix}/smart-gardening/iot/*/private_key"]
  }

  statement {
    sid = "ConsumeIngestQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.ingest.arn]
  }

  # Messages the server cannot process (e.g. they break the contract) are moved to the DLQ.
  statement {
    sid       = "SendToIngestDlq"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]
  }

  # Irrigation commands, through the iot-data Publish API (HTTPS + SigV4, no MQTT connection).
  statement {
    sid       = "PublishCommands"
    actions   = ["iot:Publish"]
    resources = ["${local.iot_arn_prefix}:topic/smart-gardening/*/commands"]
  }
}

resource "aws_iam_role_policy" "ec2" {
  name   = "smart-gardening-ec2"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2.json
}
