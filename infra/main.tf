data "huaweicloud_availability_zones" "bookstore" {}

data "huaweicloud_account" "current" {}

locals {
  use_shared_apig             = var.shared_apig_instance_id != "" && var.shared_apig_env_id != ""
  function_network_configured = var.shared_vpc_id != "" && var.shared_network_id != ""
  apig_instance_id            = local.use_shared_apig ? var.shared_apig_instance_id : huaweicloud_apig_instance.bookstore[0].id
  apig_env_id                 = local.use_shared_apig ? var.shared_apig_env_id : huaweicloud_apig_environment.bookstore[0].id
  function_env = merge(
    {
      BOOKSTORE_NAMESPACE = var.namespace
      BOOKSTORE_MODE      = "cloud"
      JWT_SECRET          = var.jwt_secret
      GEMINI_ENDPOINT     = var.gemini_endpoint
      GEMINI_PORT         = tostring(var.gemini_port)
      GEMINI_PROTOCOL     = var.gemini_protocol
      GEMINI_REGION       = var.gemini_region
      GEMINI_ACCESS_KEY   = var.gemini_access_key
      GEMINI_SECRET_KEY   = var.gemini_secret_key
      CSS_ENDPOINT        = var.css_endpoint
      CSS_PORT            = tostring(var.css_port)
      CSS_TLS             = tostring(var.css_tls)
      CSS_TLS_VERIFY      = tostring(var.css_tls_verify)
      CSS_USERNAME        = var.css_username
      CSS_PASSWORD        = var.css_password
      CSS_INDEX           = var.search_index
      DCS_ENDPOINT        = var.dcs_endpoint
      DCS_PORT            = tostring(var.dcs_port)
      DCS_TLS             = tostring(var.dcs_tls)
      DCS_PASSWORD        = var.dcs_password
    },
    var.jwt_issuer != "" ? { JWT_ISSUER = var.jwt_issuer } : {},
    var.jwt_audience != "" ? { JWT_AUDIENCE = var.jwt_audience } : {},
    var.css_token != "" ? { CSS_TOKEN = var.css_token } : {},
  )
  api_routes = {
    books = {
      method = "ANY"
      path   = "/bookstore/v1/books"
    }
    book = {
      method = "ANY"
      path   = "/bookstore/v1/books/{book_id}"
    }
    categories = {
      method = "ANY"
      path   = "/bookstore/v1/categories"
    }
    category = {
      method = "ANY"
      path   = "/bookstore/v1/categories/{category}"
    }
    cart = {
      method = "ANY"
      path   = "/bookstore/v1/cart"
    }
    cart_item = {
      method = "ANY"
      path   = "/bookstore/v1/cart/{book_id}"
    }
    checkout = {
      method = "POST"
      path   = "/bookstore/v1/checkout"
    }
    orders = {
      method = "GET"
      path   = "/bookstore/v1/orders"
    }
    order = {
      method = "GET"
      path   = "/bookstore/v1/orders/{order_id}"
    }
    search = {
      method = "GET"
      path   = "/bookstore/v1/search"
    }
    bestsellers = {
      method = "GET"
      path   = "/bookstore/v1/bestsellers"
    }
    friend_recommendations = {
      method = "GET"
      path   = "/bookstore/v1/recommendations/friends"
    }
    book_recommendations = {
      method = "GET"
      path   = "/bookstore/v1/recommendations/books/{book_id}"
    }
  }
}

resource "huaweicloud_vpc" "bookstore" {
  count = local.use_shared_apig ? 0 : 1
  name  = "${var.app_name}-network"
  cidr  = var.vpc_cidr
}

resource "huaweicloud_vpc_subnet" "bookstore" {
  count             = local.use_shared_apig ? 0 : 1
  name              = "${var.app_name}-subnet"
  cidr              = var.subnet_cidr
  gateway_ip        = cidrhost(var.subnet_cidr, 1)
  vpc_id            = huaweicloud_vpc.bookstore[0].id
  availability_zone = data.huaweicloud_availability_zones.bookstore.names[0]
}

resource "huaweicloud_networking_secgroup" "bookstore" {
  count                = local.use_shared_apig ? 0 : 1
  name                 = "${var.app_name}-security"
  description          = "Security group for ${var.app_name} APIG"
  delete_default_rules = true
}

resource "huaweicloud_lts_group" "bookstore" {
  group_name  = "${var.app_name}-logs"
  ttl_in_days = 7
}

resource "huaweicloud_lts_stream" "bookstore" {
  group_id    = huaweicloud_lts_group.bookstore.id
  stream_name = "${var.app_name}-function"
}

resource "huaweicloud_identity_agency" "function_vpc" {
  name                  = "${var.app_name}-function-vpc"
  description           = "Allow the bookstore FunctionGraph function to use the shared VPC"
  delegated_domain_name = "op_svc_cff"
  duration              = "FOREVER"

  project_role {
    project = var.region
    roles   = ["VPC FullAccess"]
  }
}

resource "huaweicloud_fgs_function" "bookstore" {
  name           = "${var.app_name}-function"
  app            = "default"
  handler        = "app.handler"
  runtime        = "Python3.10"
  memory_size    = 128
  timeout        = 15
  code_type      = "inline"
  func_code      = base64encode(file("${path.module}/../src/app.py"))
  user_data      = jsonencode(local.function_env)
  agency         = huaweicloud_identity_agency.function_vpc.name
  enable_lts_log = true
  vpc_id         = local.function_network_configured ? var.shared_vpc_id : null
  network_id     = local.function_network_configured ? var.shared_network_id : null

  log_group_id    = huaweicloud_lts_group.bookstore.id
  log_group_name  = huaweicloud_lts_group.bookstore.group_name
  log_stream_id   = huaweicloud_lts_stream.bookstore.id
  log_stream_name = huaweicloud_lts_stream.bookstore.stream_name
}

resource "huaweicloud_apig_instance" "bookstore" {
  count             = local.use_shared_apig ? 0 : 1
  name              = "${var.app_name}-instance"
  edition           = "BASIC"
  vpc_id            = huaweicloud_vpc.bookstore[0].id
  subnet_id         = huaweicloud_vpc_subnet.bookstore[0].id
  security_group_id = huaweicloud_networking_secgroup.bookstore[0].id
  bandwidth_size    = 5

  ingress_bandwidth_size          = 5
  ingress_bandwidth_charging_mode = "bandwidth"
  available_zones                 = [data.huaweicloud_availability_zones.bookstore.names[0]]
}

resource "huaweicloud_apig_group" "bookstore" {
  instance_id           = local.apig_instance_id
  name                  = "${var.app_name}-group"
  description           = "Bookstore API group"
  domain_access_enabled = true
}

resource "huaweicloud_apig_environment" "bookstore" {
  count       = local.use_shared_apig ? 0 : 1
  instance_id = huaweicloud_apig_instance.bookstore[0].id
  name        = "bookstore-prod"
  description = "Bookstore production validation environment"
}

resource "huaweicloud_apig_api" "bookstore" {
  for_each = local.api_routes

  instance_id             = local.apig_instance_id
  group_id                = huaweicloud_apig_group.bookstore.id
  type                    = "Public"
  name                    = "${var.app_name}-${each.key}"
  request_protocol        = "HTTPS"
  request_method          = each.value.method
  request_path            = each.value.path
  security_authentication = "NONE"
  matching                = "Exact"

  func_graph {
    function_urn     = trimsuffix(huaweicloud_fgs_function.bookstore.urn, ":latest")
    version          = "latest"
    network_type     = "V1"
    request_protocol = "HTTPS"
    timeout          = 15000
    invocation_type  = "sync"
  }
}

resource "huaweicloud_apig_api_publishment" "bookstore" {
  for_each = local.api_routes

  instance_id = local.apig_instance_id
  env_id      = local.apig_env_id
  api_id      = huaweicloud_apig_api.bookstore[each.key].id
  description = "Published by ${var.app_name}"
}
