variable "region" {
  description = "华为云区域"
  type        = string
  default     = "cn-north-4"
}

variable "app_name" {
  description = "应用前缀；所有资源名称均由该前缀派生"
  type        = string
  default     = "bookstore-hw"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{2,30}$", var.app_name))
    error_message = "app_name 必须以字母开头，仅含字母、数字、连字符，长度为 3~31。"
  }
}

variable "namespace" {
  description = "GeminiDB/CSS/DCS 的应用数据命名空间"
  type        = string
  default     = "bookstore_hw"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9_]{2,30}$", var.namespace))
    error_message = "namespace 必须以字母开头，仅含字母、数字、下划线，长度为 3~31。"
  }
}

variable "shared_apig_instance_id" {
  description = "共享 APIG 专享实例 ID；为空时创建独立实例"
  type        = string
  default     = ""
}

variable "shared_apig_env_id" {
  description = "共享 APIG 环境 ID；需与 shared_apig_instance_id 同时为空或非空"
  type        = string
  default     = ""
}

variable "shared_vpc_id" {
  description = "FunctionGraph 访问 GeminiDB 私网数据面的共享 VPC ID；为空时函数不挂接网络"
  type        = string
  default     = ""
}

variable "shared_network_id" {
  description = "FunctionGraph 共享子网的 network_id；必须与 shared_vpc_id 同时填写"
  type        = string
  default     = ""
}

variable "vpc_cidr" {
  description = "独立 APIG 网络网段"
  type        = string
  default     = "192.168.0.0/16"
}

variable "subnet_cidr" {
  description = "独立 APIG 子网网段"
  type        = string
  default     = "192.168.10.0/24"
}

variable "jwt_secret" {
  description = "受控 JWT HS256 密钥；生产环境通过 TF_VAR_jwt_secret 注入"
  type        = string
  sensitive   = true
  default     = ""
}

variable "jwt_issuer" {
  description = "JWT issuer，留空则不校验 issuer"
  type        = string
  default     = ""
}

variable "jwt_audience" {
  description = "JWT audience，留空则不校验 audience"
  type        = string
  default     = ""
}

variable "gemini_endpoint" {
  description = "GeminiDB DynamoDB-compatible 数据面主机或 URL（不含凭据）"
  type        = string
  default     = ""
}

variable "gemini_port" {
  description = "GeminiDB DynamoDB-compatible 数据面端口"
  type        = number
  default     = 8000

  validation {
    condition     = var.gemini_port > 0 && var.gemini_port < 65536
    error_message = "gemini_port 必须是 1~65535 的端口。"
  }
}

variable "gemini_protocol" {
  description = "GeminiDB 数据面协议，仅支持 http 或 https"
  type        = string
  default     = "http"

  validation {
    condition     = contains(["http", "https"], lower(var.gemini_protocol))
    error_message = "gemini_protocol 必须是 http 或 https。"
  }
}

variable "gemini_region" {
  description = "GeminiDB DynamoDB SigV4 credential scope 使用的区域"
  type        = string
  default     = "cn-north-4"
}

variable "gemini_access_key" {
  description = "GeminiDB DynamoDB SigV4 access key；通过 TF_VAR_gemini_access_key 注入"
  type        = string
  sensitive   = true
  default     = ""
}

variable "gemini_secret_key" {
  description = "GeminiDB DynamoDB SigV4 secret key；通过 TF_VAR_gemini_secret_key 注入"
  type        = string
  sensitive   = true
  default     = ""
}

variable "css_endpoint" {
  description = "CSS/OpenSearch-compatible endpoint"
  type        = string
  default     = ""
}

variable "css_port" {
  description = "CSS HTTPS/HTTP 端口"
  type        = number
  default     = 443
}

variable "css_tls" {
  description = "CSS 是否使用 TLS"
  type        = bool
  default     = true
}

variable "css_tls_verify" {
  description = "是否校验 CSS TLS 服务端证书；生产环境必须保持 true"
  type        = bool
  default     = true
}

variable "css_username" {
  description = "CSS 基本认证用户名"
  type        = string
  sensitive   = true
  default     = ""
}

variable "css_password" {
  description = "CSS 基本认证密码"
  type        = string
  sensitive   = true
  default     = ""
}

variable "css_token" {
  description = "CSS 可选 Bearer token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "dcs_endpoint" {
  description = "DCS Redis endpoint 或主机名"
  type        = string
  default     = ""
}

variable "dcs_port" {
  description = "DCS Redis 端口"
  type        = number
  default     = 6379
}

variable "dcs_tls" {
  description = "DCS 是否使用 TLS"
  type        = bool
  default     = true
}

variable "dcs_password" {
  description = "DCS Redis 密码"
  type        = string
  sensitive   = true
  default     = ""
}

variable "search_index" {
  description = "CSS 书籍索引名；必须以 namespace- 开头，避免共享索引串数据"
  type        = string
  default     = "bookstore_hw-books"

  validation {
    condition     = startswith(var.search_index, "${var.namespace}-")
    error_message = "search_index 必须以 namespace- 开头。"
  }
}

variable "frontend_bucket" {
  description = "可选 OBS 静态站桶名，仅作为上传脚本默认值，不创建桶"
  type        = string
  default     = ""
}

variable "frontend_prefix" {
  description = "OBS 静态站对象前缀"
  type        = string
  default     = "bookstore/"
}

variable "eg_agency_name" {
  description = "保留给共享事件扩展的模式专属委托名；本示例默认不创建 EG 资源"
  type        = string
  default     = "bookstore-hw-eg"
}

check "shared_apig_pair" {
  assert {
    condition     = (var.shared_apig_instance_id == "") == (var.shared_apig_env_id == "")
    error_message = "shared_apig_instance_id 与 shared_apig_env_id 必须同时为空或同时填写。"
  }
}

check "shared_function_network_pair" {
  assert {
    condition     = (var.shared_vpc_id == "") == (var.shared_network_id == "")
    error_message = "shared_vpc_id 与 shared_network_id 必须同时为空或同时填写。"
  }
}
