variable "database" {
  type = string
}

variable "product_id" {
  type = string
}

variable "product" {
  description = "One entry of products in products.auto.tfvars.json, rendered by `dpcp render` from the descriptor."
  type = object({
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
  })
}

variable "team_role" {
  description = "The owning team's functional role; it is granted the writer and full-reader roles."
  type        = string
}

variable "serve_schema" {
  type    = string
  default = "SERVE"
}

variable "kafka_partitions" {
  type    = number
  default = 6
}

variable "kafka_replication" {
  type    = number
  default = 3
}
