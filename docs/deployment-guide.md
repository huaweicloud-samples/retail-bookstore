# 部署指南

本文给出从零到可验证部署的完整命令，每一步都附预期输出。
命令默认在本仓库根目录执行，需要进入子目录时会显式写出 `cd`。

架构与设计取舍见 [architecture.md](architecture.md)。

## 0. 约定与占位符

| 占位符 | 含义 | 取值方式 |
|-|-|-|
| `<HW_ACCESS_KEY>` | Access Key ID | 控制台「我的凭证」页面的访问密钥区域 |
| `<HW_SECRET_KEY>` | Secret Access Key | 同上；两者只填入当前 shell，不要写入代码或提交到仓库 |
| `<数据面地址>` | 文档数据服务的数据面地址 | 由实例详情页获取 |
| `<检索服务地址>` | 检索引擎的访问地址 | 由实例详情页获取 |
| `<缓存服务地址>` | 缓存实例的访问地址 | 由实例详情页获取 |

## 1. 自检本地工具

```bash
python --version
terraform version
```

预期输出（版本不低于下表要求即可）：

```text
Python 3.11.9
Terraform v1.9.8
```

要求：Terraform CLI `>= 1.5`，huaweicloud Provider `>= 1.95`，Python 3.9 及以上。

## 2. 本地单元测试（部署前可做）

```bash
cd src
python -m pytest tests -q
cd ..
```

预期输出：

```text
13 passed in 0.03s
```

测试覆盖令牌校验、领域规则、样例夹具处理与数据面适配，全部使用内存实现，不访问云端。

## 3. 静态校验

```bash
cd infra
terraform init
terraform validate
```

预期输出：

```text
Success! The configuration is valid, but there were some
validation warnings as shown above.
```

未使用官方插件镜像时，警告内容是本地开发覆盖的提示，属预期输出。

## 4. 配置访问凭据

```bash
export HW_ACCESS_KEY=<HW_ACCESS_KEY>
export HW_SECRET_KEY=<HW_SECRET_KEY>
```

## 5. 提供部署参数

分为三组：入口与网络、数据面凭据、检索与缓存。

```bash
# 入口与网络
export TF_VAR_shared_apig_instance_id="<APIG 专享实例 ID>"
export TF_VAR_shared_apig_env_id="<共享环境 ID>"

# 数据面
export TF_VAR_gemini_endpoint="<数据面地址>"
export TF_VAR_gemini_access_key="<数据面访问密钥 ID>"
export TF_VAR_gemini_secret_key="<数据面访问密钥 Secret>"

# 检索与缓存
export TF_VAR_css_endpoint="<检索服务地址>"
export TF_VAR_css_username="<检索用户名>"
export TF_VAR_css_password="<检索密码>"
export TF_VAR_dcs_endpoint="<缓存服务地址>"
export TF_VAR_dcs_password="<缓存密码>"

# 令牌密钥
export TF_VAR_jwt_secret="<令牌签名密钥>"
```

### 5.1 主要参数

| 变量 | 类型 | 默认值 | 必填 | 说明 |
|-|-|-|-|-|
| `region` | string | `cn-north-4` | 否 | 部署区域 |
| `app_name` | string | `bookstore-hw` | 否 | 资源名称前缀 |
| `namespace` | string | `bookstore_hw` | 否 | 数据命名空间；字母开头，可含下划线 |
| `shared_apig_instance_id` | string | 空 | 建议 | 共享网关实例 ID；与 `shared_apig_env_id` 成对 |
| `shared_apig_env_id` | string | 空 | 建议 | 共享环境 ID；与 `shared_apig_instance_id` 成对 |
| `shared_vpc_id` | string | 空 | 建议 | 共享网络 ID；与 `shared_network_id` 成对 |
| `shared_network_id` | string | 空 | 建议 | 共享子网 network_id；与 `shared_vpc_id` 成对 |
| `vpc_cidr` / `subnet_cidr` | string | `192.168.0.0/16` / `192.168.10.0/24` | 否 | 独立部署时创建的网络网段 |
| `jwt_secret` | string | 空 | 是 | 令牌签名密钥（敏感） |
| `jwt_issuer` / `jwt_audience` | string | 空 | 否 | 留空则不校验 |
| `gemini_endpoint` | string | 空 | 是 | 数据面地址 |
| `gemini_port` / `gemini_protocol` / `gemini_region` | — | `8000` / `http` / `cn-north-4` | 否 | 数据面连接与签名 |
| `gemini_access_key` / `gemini_secret_key` | string | 空 | 是 | 数据面凭据（敏感） |
| `css_endpoint` / `css_port` / `css_tls` / `css_tls_verify` | — | 空 / `443` / `true` / `true` | 是 | 检索服务接入 |
| `css_username` / `css_password` / `css_token` | string | 空 | 按需 | 检索认证（敏感） |
| `dcs_endpoint` / `dcs_port` / `dcs_tls` / `dcs_password` | — | 空 / `6379` / `true` / 空 | 是 | 缓存服务接入 |
| `search_index` | string | `bookstore_hw-books` | 否 | 索引名；必须以 `<namespace>-` 开头 |
| `frontend_bucket` / `frontend_prefix` | string | 空 / `bookstore/` | 否 | 上传脚本的默认目标，不创建桶 |

### 5.2 几处容易填错的位置

- 网关实例与环境必须成对提供，只填一个会在计划阶段由 `check` 块报错。
- 共享网络与子网必须成对，否则函数无法访问私网数据面。
- `search_index` 必须以 `namespace-` 开头，避免多个部署共用索引时串数据。
- `css_tls_verify` 生产环境必须保持 `true`。
- 令牌密钥与数据面凭据都是敏感变量，状态文件需按密文管理。

## 6. 装载样例数据

首次部署后需要写入书目、好友关系与购买记录，并创建检索索引：

```bash
python src/scripts/seed.py
```

脚本从 `src/fixtures/` 读取固定样例数据，写入六张表并建索引。重复执行是幂等的。

预期输出：

```text
[INFO] books: 8 written
[INFO] friends: 3 written
[INFO] purchases: 5 written
[INFO] index <namespace>-books ready
```

## 7. 创建资源

```bash
cd infra
terraform plan -out=bookstore.tfplan
terraform apply bookstore.tfplan
```

`terraform plan` 预期会创建以下资源（复用共享网关时）：

```text
Plan: 19 to add, 0 to change, 0 to destroy.
```

构成：1 个日志组与 1 条日志流、1 个委托、1 个函数、1 个分组、
13 条 API、13 条发布记录。

自建网关实例时另加 1 个网络、1 个子网、1 个安全组与 1 个网关实例，
并把环境一并创建，共 24 项。

`terraform apply` 完成后预期输出：

```text
Apply complete! Resources: 19 added, 0 changed, 0 destroyed.
```

## 8. 读取部署输出

```bash
terraform output
```

预期列出以下输出名：

```text
api_base_url               apig_env_id            apig_instance_id
data_plane_probe           function_name          function_network_configured
function_urn               log_group_id           log_stream_id
namespace                  region
```

常用取值：

```bash
terraform output api_base_url                 # 书店 API 基地址（含 /bookstore/v1）
terraform output function_name                # 函数名
terraform output data_plane_probe             # 三个数据面的配置状态（不回显凭据）
```

## 9. 验证部署

```bash
python src/scripts/verify_e2e.py
```

脚本读取部署输出，对真实网关地址跑十六项校验，并把结果写入 `verification-result.json`。

预期输出结尾（摘要字段）：

```json
{"passed": true, "checks": 16}
```

十六项覆盖：书目列表、单本书目、类目列表、类目下书目、搜索、加购、购物车读取（两次）、
再加一件、结算（两次，验证幂等）、订单列表、单笔订单、畅销榜、好友推荐、相关书目推荐。

## 10. 手工验证示例

```bash
export BASE="$(terraform output -raw api_base_url)"
export TOKEN="<由 src/scripts/make_test_token 生成的令牌>"
```

取书目列表：

```bash
curl -s "$BASE/books" -H "Authorization: Bearer $TOKEN" | head -c 300
```

预期返回书目数组（样例数据 8 本）。

搜索：

```bash
curl -s "$BASE/search?q=python" -H "Authorization: Bearer $TOKEN"
```

预期返回命中结果（样例数据中 1 条）。

加购与结算：

```bash
curl -X PUT "$BASE/cart/book-001" -H "Authorization: Bearer $TOKEN"
curl -X POST "$BASE/checkout" -H "Authorization: Bearer $TOKEN"
```

结算预期返回 201 与订单标识；重复提交同一购物车不会产生重复订单。

畅销榜与推荐：

```bash
curl -s "$BASE/bestsellers" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/recommendations/friends" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/recommendations/books/book-001" -H "Authorization: Bearer $TOKEN"
```

预期分别返回 5 条、3 条、2 条（与样例数据一致）。

### 10.1 验证租户边界

不带令牌或使用无效令牌访问任一读写接口，预期返回 401：

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/books"
```

预期输出 `401`。用一个用户的令牌读取另一个用户的购物车不会返回对方数据——
用户边界只由令牌的 `sub` 决定。

**部署完成后请按本节步骤在真实环境自行验证，确认功能可用后方可视为交付完成。**

## 11. 重建读模型

书目数据变更后，可重建检索索引与畅销榜：

```bash
python src/scripts/rebuild_projections.py
```

预期输出：

```text
[INFO] index <namespace>-books rebuilt: 8 documents
[INFO] bestsellers rebuilt: <数量> entries
```

## 12. 上传前端静态站

```bash
python src/scripts/upload_obs.py --dist src/frontend/dist
```

脚本只上传到既有桶（由 `frontend_bucket` 与 `frontend_prefix` 指定），不创建桶、
不声明托管形态。前端构建产物先由以下命令生成：

```bash
node src/frontend/build.mjs
```

## 13. 释放资源

```bash
cd infra
terraform destroy
```

预期输出：

```text
Destroy complete! Resources: 19 destroyed.
```

销毁会删除函数、分组/环境/API/发布、日志组与日志流、委托；自建实例时一并删除网络与网关实例。
数据面实例、检索实例、缓存实例与静态桶属共享资源，不在本目录的清理范围。
需要清空本命名空间的数据时，执行 `python src/scripts/cleanup.py`（只作用于本命名空间的表与索引）。

## 14. 故障排查

| 现象 | 定位方向 |
|-|-|
| 函数报「找不到入口」 | 单文件内联部署，入口必须是 `app.handler` |
| 全部接口返回 401 | 令牌缺失、签名密钥不一致或已过期 |
| 接口返回 502 | 数据面调用异常：核对地址、端口、协议、签名区域与凭据 |
| 搜索无结果 | 索引是否已创建并写入；`search_index` 是否与装载时一致 |
| 畅销榜为空 | 是否已完成至少一次结算；缓存键前缀是否与 `namespace` 一致 |
| 结算重复下单 | 幂等依赖购物车快照与订单去重，检查是否清空了购物车 |
| 函数无法访问私网数据面 | 共享网络与子网是否成对配置；函数是否挂接成功（看输出 `function_network_configured`） |
| 日志为空 | 函数必须同时绑定日志组 ID/名称与日志流 ID/名称四个参数 |
