output "roles" {
  value = {
    reader      = snowflake_account_role.reader.name
    reader_full = snowflake_account_role.reader_full.name
    writer      = snowflake_account_role.writer.name
  }
}
