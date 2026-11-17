data "huaweicloud_apig_groups" "bookstore" {
  instance_id = local.apig_instance_id
  group_id    = huaweicloud_apig_group.bookstore.id
}

output "region" {
  description = "部署区域"
  value       = var.region
}

output "namespace" {
  description = "应用数据命名空间"
  value       = var.namespace
}

output "apig_instance_id" {
  description = "APIG 专享实例 ID（共享或本目录创建）"
  value       = local.apig_instance_id
}

output "apig_env_id" {
  description = "APIG 环境 ID（共享或本目录创建）"
  value       = local.apig_env_id
}

output "api_base_url" {
  description = "书店 API 基地址"
  value       = "https://${data.huaweicloud_apig_groups.bookstore.groups[0].sl_domain}/bookstore/v1"
}

output "function_urn" {
  description = "FunctionGraph 入口函数 URN"
  value       = huaweicloud_fgs_function.bookstore.urn
}

output "function_name" {
  description = "FunctionGraph 入口函数名"
  value       = huaweicloud_fgs_function.bookstore.name
}

output "function_network_configured" {
  description = "是否为 FunctionGraph 注入共享 VPC 与 network_id"
  value       = local.function_network_configured
}

output "log_group_id" {
  description = "LTS 日志组 ID"
  value       = huaweicloud_lts_group.bookstore.id
}

output "log_stream_id" {
  description = "LTS 日志流 ID"
  value       = huaweicloud_lts_stream.bookstore.id
}

output "data_plane_probe" {
  description = "数据面前置探针提示；endpoint 由变量注入，不在 output 中回显凭据"
  value = {
    geminidb = var.gemini_endpoint != "" ? "configured" : "not-configured"
    css      = var.css_endpoint != "" ? "configured" : "not-configured"
    dcs      = var.dcs_endpoint != "" ? "configured" : "not-configured"
  }
}
