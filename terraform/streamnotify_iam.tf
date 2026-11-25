# IAM role for the streamnotify ServiceAccount (IRSA) + SES sender identity.
# Variables expected: var.environment, var.oidc_provider_arn, var.oidc_provider_url, var.aws_region
# (same names the other modules in infra_aws/dev already use).
data "aws_caller_identity" "current" {}

resource "aws_iam_role" "streamnotify" {
  name = "streamnotify-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = var.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "${replace(var.oidc_provider_url, "https://", "")}:sub" = "system:serviceaccount:default:streamnotify"
        "${replace(var.oidc_provider_url, "https://", "")}:aud" = "sts.amazonaws.com"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "streamnotify" {
  name = "streamnotify-messaging"
  role = aws_iam_role.streamnotify.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ses:SendEmail"], Resource = "*" },
      # SNS direct-to-phone SMS needs Resource "*" (phone numbers are not ARNs)
      { Effect = "Allow", Action = ["sns:Publish"], Resource = "*" }
    ]
  })
}

variable "ses_sender" {
  type    = string
  default = "windevopscloud+sender@gmail.com"
}

# After apply, click the verification link SES emails to the sender address.
resource "aws_sesv2_email_identity" "sender" {
  email_identity = var.ses_sender
}
