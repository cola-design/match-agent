# Dify 同服务器部署 MongoDB API（Linux + Docker Compose）

此服务是独立容器，不是 Dify Python 沙箱的一部分。Dify 调 HTTP API，API 使用只读账号查询 MongoDB。所有数据操作均为读取。

```text
Dify HTTP节点 → match-agent-mongo-api:8000 → MongoDB:27017
```

## 1. 在 Dify 服务器执行，不是在个人 Windows 电脑执行

请先通过 SSH 登录 Dify 服务器，再检查：

```bash
docker version
docker compose version
docker ps --format 'table {{.Names}}\t{{.Image}}'
```

以下命令使用已有 Docker 操作权限；没有权限时由服务器管理员执行，不要改 Docker socket 权限。

## 2. 下载独立部署包

```bash
mkdir -p ~/match-agent-api
cd ~/match-agent-api
curl -fL 'https://raw.githubusercontent.com/cola-design/match-agent/main/mongodb_api_server_bundle.zip' -o api.zip
unzip api.zip
cd mongodb_api
```

如果服务器无法访问 GitHub，可在电脑下载 ZIP 后，通过 SSH 文件传输上传、解压到服务器。

## 3. 确认 Dify 的真实 Docker 网络名

从第一步列出的容器里找到 Dify API 和 ssrf_proxy（名称通常包含 api、ssrf_proxy），分别执行：

```bash
docker inspect --format '{{json .NetworkSettings.Networks}}' 实际的Dify_API容器名
docker inspect --format '{{json .NetworkSettings.Networks}}' 实际的ssrf_proxy容器名
```

选择两者共用、且能对外访问 MongoDB 的网络。常见名字是 `docker_default`，但不能直接假定你的名字相同。把实际网络名填入下一步 `DIFY_NETWORK`。

如果使用自定义 OpenAPI 工具，还要确认实际执行工具的 plugin_daemon 能访问此网络。建议先用原生 HTTP 请求节点接入。

现有网络必须已经存在；本部署不会重启或修改 Dify。不要把 API 只接入 `internal: true` 的隔离网络，否则它可能无法访问 MongoDB。

## 4. 在服务器本地配置凭据

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

修改以下值：

```dotenv
DIFY_NETWORK=实际的Dify共享网络名
MONGODB_URI='mongodb://match_agent_api:实际密码@完整MongoDB内网IP:27017/?authSource=admin'
MONGODB_DB=match_agent
MONGODB_API_KEY='自行生成的长随机密钥'
```

URI 的用户名或密码如包含保留字符，先做 URL 编码，不要直接拼接。不要把真实 `.env` 上传 GitHub。

可以执行以下命令生成 API 密钥；复制结果到 `.env` 的 MONGODB_API_KEY：

```bash
openssl rand -hex 32
```

`.env.example` 中的占位密钥不能直接使用，服务会拒绝启动。Dify 只需保存 API 密钥，不需保存 MongoDB 密码。

## 5. 构建并后台启动

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 match-agent-mongo-api
```

首次构建需要服务器能访问 Docker 镜像源和 Python 包源；离线环境需由管理员配置内部镜像源。

`restart: unless-stopped` 让容器在进程退出或服务器重启后恢复，前提是 Docker 随系统启动；人工停止的容器不会自行重新启动。

`healthy` 只表示 `/live` 返回正常，数据库认证须完成下一步。Docker 仅标记 unhealthy 时不会自动重启仍在运行的进程。

## 6. 验证 API 真正连接数据库

下面命令在 API 容器内部读取密钥，不把密钥写进命令或显示在结果中：

```bash
docker compose exec -T match-agent-mongo-api python - <<'PY'
import json
import os
import urllib.request
req = urllib.request.Request(
    'http://127.0.0.1:8000/health',
    headers={'X-API-Key': os.environ['MONGODB_API_KEY']}
)
with urllib.request.urlopen(req, timeout=12) as response:
    print(json.load(response))
PY
```

应返回 `{"status": "ok", "database": "match_agent"}`。这里的 `127.0.0.1` 位于 API 容器内，因此正确。

如果返回 503，数据库连接/认证还未成功，先看 API 日志并验证该容器到 MongoDB 的网络；不要先改 Dify。

## 7. 从 Dify HTTP 节点验证访问

在独立临时流程里添加一个 HTTP 请求节点：

- 方法：GET
- URL：`http://match-agent-mongo-api:8000/health`
- Header 名称：`X-API-Key`
- Header 值：引用 Dify 的 Secret 环境变量，填写服务器 `.env` 中相同的 API 密钥

返回 `status_code=200` 且 body 为健康结果后，再替换正式流程的产品/价格检索节点。

不要在 Dify 填 `127.0.0.1:8000`：Dify、API、ssrf_proxy 分属不同容器，localhost 指向各自容器。

部署默认只将主机 `127.0.0.1:18000` 映射到 API，不公开内网或公网的 8000 端口。容器间通过共享网络和服务名访问，走内部 HTTP；跨服务器或不可信网络需要配置 HTTPS。

如果 Dify 返回 403、名称解析失败或无法连接，检查实际发请求的服务（尤其 ssrf_proxy）的网络、DNS、访问日志及定向访问规则，不要关闭全部 SSRF 保护。

## 8. 正式查询接口

```text
GET /v1/products/search?q=型号或规格&limit=30
GET /v1/products/物料编码
GET /v1/prices/物料编码?as_of=YYYY-MM-DD
```

产品过滤：`data_status=ACTIVE`、`source_status=有效`。

价格过滤：`status=ACTIVE` 且有效期覆盖报价日期，缺省日期按北京时间。

候选搜索只负责召回，后续仍需工作流校验类别和全部规格，不得直接采用第一条结果。多个有效价格同时返回时，应保留冲突供工作流阻断，不自动选择某一条。

API 不会把已入库的估算价变成经过市场核验的采购价，`price_basis` 原样返回。

## 9. 日常维护

```bash
docker compose ps
docker compose logs --tail=100 match-agent-mongo-api
docker compose restart match-agent-mongo-api
```

只修改 `.env` 后，用下面命令让新配置生效；普通 restart 不会重新载入 Compose 环境变量：

```bash
docker compose up -d --force-recreate
```

停止服务：`docker compose stop`；恢复服务：`docker compose up -d`。本服务不包含 MongoDB 容器，不会删除产品库或价格库。

日志自动轮转，最多约30MB。API只读账号不能执行后续同步写入；定时清洗同步应使用独立任务和独立写入账号。
