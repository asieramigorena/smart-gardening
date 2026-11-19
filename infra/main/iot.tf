# AWS IoT Core: one thing + certificate per device, the device policy, and the topic rule that
# forwards readings and events to the ingestion queue. Topics and payloads are defined in
# docs/contracts/README.md.

data "aws_iot_endpoint" "data" {
  endpoint_type = "iot:Data-ATS"
}

locals {
  iot_arn_prefix = "arn:${data.aws_partition.current.partition}:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}"
  # IoT policy variable (resolved by IoT Core at connection time, not by Terraform: hence $$).
  iot_thing_name = "$${iot:Connection.Thing.ThingName}"
}

resource "aws_iot_thing" "device" {
  for_each = toset(var.devices)

  name = each.key
}

# AWS generates the key pair. The private key is stored in SSM (see secrets.tf) and, like every
# generated secret, in the Terraform state (private, encrypted bucket; accepted for the MVP).
resource "aws_iot_certificate" "device" {
  for_each = toset(var.devices)

  active = true
}

resource "aws_iot_thing_principal_attachment" "device" {
  for_each = toset(var.devices)

  thing     = aws_iot_thing.device[each.key].name
  principal = aws_iot_certificate.device[each.key].arn
}

# One policy for every device: the policy variable limits each device to its own client ID and
# topics. It only works if the certificate is attached to the thing (checked on Connect).
data "aws_iam_policy_document" "iot_device" {
  statement {
    sid       = "ConnectAsOwnThing"
    actions   = ["iot:Connect"]
    resources = ["${local.iot_arn_prefix}:client/${local.iot_thing_name}"]

    condition {
      test     = "Bool"
      variable = "iot:Connection.Thing.IsAttached"
      values   = ["true"]
    }
  }

  statement {
    sid     = "PublishReadingsAndEvents"
    actions = ["iot:Publish"]
    resources = [
      "${local.iot_arn_prefix}:topic/smart-gardening/${local.iot_thing_name}/readings",
      "${local.iot_arn_prefix}:topic/smart-gardening/${local.iot_thing_name}/events",
    ]
  }

  statement {
    sid       = "SubscribeCommands"
    actions   = ["iot:Subscribe"]
    resources = ["${local.iot_arn_prefix}:topicfilter/smart-gardening/${local.iot_thing_name}/commands"]
  }

  statement {
    sid       = "ReceiveCommands"
    actions   = ["iot:Receive"]
    resources = ["${local.iot_arn_prefix}:topic/smart-gardening/${local.iot_thing_name}/commands"]
  }
}

resource "aws_iot_policy" "device" {
  name   = "smart-gardening-device"
  policy = data.aws_iam_policy_document.iot_device.json
}

resource "aws_iot_policy_attachment" "device" {
  for_each = toset(var.devices)

  policy = aws_iot_policy.device.name
  target = aws_iot_certificate.device[each.key].arn
}

# Topic rule: readings and events go to the ingestion queue, enriched with the device ID taken
# from the topic (it overrides any device_id in the payload), the message kind and the time
# IoT Core received it (ms since epoch). Commands are not forwarded.
resource "aws_iot_topic_rule" "ingest" {
  name        = "smart_gardening_ingest" # rule names only allow letters, digits and _
  description = "Forward device readings and events to the ingestion SQS queue."
  enabled     = true
  sql_version = "2016-03-23"
  sql         = "SELECT *, topic(2) AS device_id, topic(3) AS kind, timestamp() AS received_at FROM 'smart-gardening/+/+' WHERE topic(3) = 'readings' OR topic(3) = 'events'"

  sqs {
    queue_url  = aws_sqs_queue.ingest.id
    role_arn   = aws_iam_role.iot_rule.arn
    use_base64 = false
  }

  # If the SQS action fails, the message (with the error details) goes to the DLQ instead.
  error_action {
    sqs {
      queue_url  = aws_sqs_queue.ingest_dlq.id
      role_arn   = aws_iam_role.iot_rule.arn
      use_base64 = false
    }
  }

  depends_on = [aws_iam_role_policy.iot_rule]
}

# Role assumed by IoT Core to run the rule's actions.
data "aws_iam_policy_document" "iot_rule_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["iot.amazonaws.com"]
    }

    # Confused-deputy protection: only IoT Core acting for this account can assume it.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "iot_rule" {
  name               = "smart-gardening-iot-rule"
  description        = "Smart gardening IoT topic rule: send device messages to the ingestion queues."
  assume_role_policy = data.aws_iam_policy_document.iot_rule_assume_role.json
}

data "aws_iam_policy_document" "iot_rule" {
  statement {
    sid     = "SendToIngestQueues"
    actions = ["sqs:SendMessage"]
    resources = [
      aws_sqs_queue.ingest.arn,
      aws_sqs_queue.ingest_dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "iot_rule" {
  name   = "smart-gardening-iot-rule"
  role   = aws_iam_role.iot_rule.id
  policy = data.aws_iam_policy_document.iot_rule.json
}
