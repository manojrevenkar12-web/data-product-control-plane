# Production: one landing bucket, one storage integration, one warehouse and resource monitor per team, and the
# data product module for every product in products.auto.tfvars.json (`dpcp render` writes it from products/).

terraform {
  required_version = ">= 1.6"
  required_providers {
    snowflake = { source = "snowflakedb/snowflake", version = "~> 2.0" }
    aws       = { source = "hashicorp/aws", version = "~> 5.0" }
    kafka     = { source = "Mongey/kafka", version = "~> 0.8" }
  }
  backend "s3" {}
}

variable "database" {
  type = string
}

variable "products" {
  description = "Rendered by `dpcp render`; the shape is the module's `product` variable."
  type = map(object({
    table          = string
    domain         = string
    team           = string
    classification = string
    pii_columns    = list(string)
    roles          = map(string)
    source_kind    = string
    source         = string
    ports          = list(string)
    kafka_topic    = optional(string)
    inputs         = list(string)
  }))
}

data "aws_caller_identity" "current" {}

locals {
  # Built from the name, not from the role resource: the role's trust policy needs the integration's outputs.
  landing_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/snowflake-dp-landing"
}

variable "teams" {
  type = map(object({
    warehouse         = string
    size              = string
    credits_per_month = number
    email             = string
  }))
}

variable "landing_bucket" {
  type    = string
  default = "dp-landing"
}

variable "notify_users" {
  description = "Snowflake users e-mailed by resource monitors"
  type        = list(string)
  default     = []
}

resource "snowflake_account_role" "team" {
  for_each = var.teams
  name     = "TEAM_${upper(replace(each.key, "-", "_"))}"
}

resource "snowflake_resource_monitor" "team" {
  for_each                  = var.teams
  name                      = "RM_${upper(replace(each.key, "-", "_"))}"
  credit_quota              = ceil(each.value.credits_per_month)
  frequency                 = "MONTHLY"
  notify_triggers           = [75, 90]
  suspend_trigger           = 100
  suspend_immediate_trigger = 110
  notify_users              = var.notify_users
}

resource "snowflake_warehouse" "team" {
  for_each            = var.teams
  name                = each.value.warehouse
  warehouse_size      = each.value.size
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  resource_monitor    = snowflake_resource_monitor.team[each.key].name
  comment             = "Owned by ${each.key} (${each.value.email}); idle time is charged to this team"
}

resource "snowflake_grant_privileges_to_account_role" "team_warehouse" {
  for_each          = var.teams
  account_role_name = snowflake_account_role.team[each.key].name
  privileges        = ["USAGE", "OPERATE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.team[each.key].name
  }
}

resource "aws_s3_bucket" "landing" {
  bucket = var.landing_bucket
}

resource "snowflake_storage_integration" "landing" {
  name                      = "DP_LANDING"
  type                      = "EXTERNAL_STAGE"
  storage_provider          = "S3"
  enabled                   = true
  storage_aws_role_arn      = local.landing_role_arn
  storage_allowed_locations = ["s3://${aws_s3_bucket.landing.bucket}/"]
}

resource "aws_iam_role" "snowflake_landing" {
  name = "snowflake-dp-landing"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = snowflake_storage_integration.landing.storage_aws_iam_user_arn }
      Condition = { StringEquals = { "sts:ExternalId" = snowflake_storage_integration.landing.storage_aws_external_id } }
    }]
  })
}

resource "aws_iam_role_policy" "snowflake_landing_read" {
  role = aws_iam_role.snowflake_landing.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${aws_s3_bucket.landing.arn}/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.landing.arn },
    ]
  })
}

module "product" {
  source     = "../../modules/snowflake_data_product"
  for_each   = var.products
  database   = var.database
  product_id = each.key
  product    = each.value
  team_role  = snowflake_account_role.team[each.value.team].name
}
