# Ingestion queue. The IoT topic rule sends every reading and event here, and the ingestion
# service on the server consumes it. Messages that the rule cannot deliver, or that the server
# fails to process 5 times, end up in the dead-letter queue (DLQ) for inspection.

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "smart-gardening-ingest-dlq"
  message_retention_seconds = 14 * 24 * 3600 # 14 days, the SQS maximum
  sqs_managed_sse_enabled   = true           # SSE-SQS
}

resource "aws_sqs_queue" "ingest" {
  name                       = "smart-gardening-ingest"
  message_retention_seconds  = 4 * 24 * 3600 # 4 days (SQS default)
  visibility_timeout_seconds = 60
  receive_wait_time_seconds  = 20 # long polling
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
}

# Only the ingestion queue may use the DLQ as its dead-letter queue.
resource "aws_sqs_queue_redrive_allow_policy" "ingest_dlq" {
  queue_url = aws_sqs_queue.ingest_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingest.arn]
  })
}

# Queue policies. IAM identity policies grant access too, so on top of allowing the IoT rule
# role, the policies explicitly deny SendMessage to everyone else.
locals {
  # Principals allowed to send to each queue. The server moves messages it cannot parse to the DLQ.
  ingest_senders     = [aws_iam_role.iot_rule.arn]
  ingest_dlq_senders = [aws_iam_role.iot_rule.arn, aws_iam_role.ec2.arn]
}

data "aws_iam_policy_document" "ingest_queue" {
  statement {
    sid       = "AllowIotRule"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]

    principals {
      type        = "AWS"
      identifiers = local.ingest_senders
    }
  }

  statement {
    sid       = "DenyOtherSenders"
    effect    = "Deny"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = local.ingest_senders
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.ingest.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.ingest_queue.json
}

data "aws_iam_policy_document" "ingest_dlq" {
  statement {
    sid       = "AllowIotRuleAndServer"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]

    principals {
      type        = "AWS"
      identifiers = local.ingest_dlq_senders
    }
  }

  statement {
    sid       = "DenyOtherSenders"
    effect    = "Deny"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = local.ingest_dlq_senders
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.ingest_dlq.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest_dlq" {
  queue_url = aws_sqs_queue.ingest_dlq.id
  policy    = data.aws_iam_policy_document.ingest_dlq.json
}
