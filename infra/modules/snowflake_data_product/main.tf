# One data product: its access roles, the grants on its serving views, and its Kafka topic.
# Tables and views are created by the platform from the contract (`dpcp apply`) before this runs.

terraform {
  required_providers {
    snowflake = { source = "snowflakedb/snowflake" }
    kafka     = { source = "Mongey/kafka" }
  }
}

locals {
  serve  = "\"${var.database}\".\"${var.serve_schema}\""
  masked = "${local.serve}.\"${var.product.table}\""
  full   = "${local.serve}.\"${var.product.table}__FULL\""
}

resource "snowflake_account_role" "reader" {
  name    = var.product.roles["reader"]
  comment = "Reads ${var.product_id} with personal data masked"
}

resource "snowflake_account_role" "reader_full" {
  name    = var.product.roles["reader_full"]
  comment = "Reads ${var.product_id} unmasked; granted inside the ${var.product.domain} domain only (policies/access.rego)"
}

resource "snowflake_account_role" "writer" {
  name    = var.product.roles["writer"]
  comment = "The platform's ingestion for ${var.product_id}"
}

resource "snowflake_grant_privileges_to_account_role" "read_masked" {
  account_role_name = snowflake_account_role.reader.name
  privileges        = ["SELECT"]
  on_schema_object {
    object_type = "VIEW"
    object_name = local.masked
  }
}

resource "snowflake_grant_privileges_to_account_role" "read_full" {
  account_role_name = snowflake_account_role.reader_full.name
  privileges        = ["SELECT"]
  on_schema_object {
    object_type = "VIEW"
    object_name = local.full
  }
}

resource "snowflake_grant_account_role" "team_writes" {
  role_name        = snowflake_account_role.writer.name
  parent_role_name = var.team_role
}

resource "snowflake_grant_account_role" "team_reads_full" {
  role_name        = snowflake_account_role.reader_full.name
  parent_role_name = var.team_role
}

resource "kafka_topic" "published" {
  count              = var.product.kafka_topic == null ? 0 : 1
  name               = var.product.kafka_topic
  partitions         = var.kafka_partitions
  replication_factor = var.kafka_replication
  config = {
    "cleanup.policy" = "delete"
    "retention.ms"   = "604800000"
  }
}
