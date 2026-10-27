# 先用电脑运行 API，联调 Dify YAML

本方案不部署或重启 Dify 服务器。电脑暂时作为 API 服务器，Dify 使用原生 HTTP 节点访问；不在 Dify 代码节点中运行 PyMongo。

包内两个 YAML：

- `dify_http_api_connection_check.yml`：先验证 Dify 到电脑 API 的连接。
- `formal_selection_quote_v18_1_http_api.yml`：连接检查通过后，再测试完整选型报价流程。基于 V18.0，仅替换五个检索位置为 HTTP API 和响应校验；沿用人工确认、资料提取、Excel 模板及计算规则。

## 1. 让 API 接收来自 Dify 的连接

当前 API 若启动参数是 `--host 127.0.0.1`，只有本机能访问。在原来启动 API 的 PowerShell 窗口按 Ctrl+C，然后在同一个窗口执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

必须保留原窗口中的 `MONGODB_URI`、`MONGODB_DB` 和 `MONGODB_API_KEY` 环境变量；新开窗口不会自动继承原窗口后来设置的变量。

服务运行期间保持终端打开、电脑联网。不要把 `0.0.0.0` 填成 Dify 的访问地址，它只是监听设置。

## 2. 查看电脑 IP（这次不是 MongoDB IP）

另开 PowerShell，执行：

```powershell
ipconfig
```

找到正在使用的以太网或无线网卡的 IPv4 地址。如果通过 VPN 访问公司网络，需确认哪一个地址可从 Dify 服务器访问。

本次 API 地址格式：`http://电脑IPv4:8000`，末尾不加斜杠。

- 不能填 MongoDB 的地址或 27017 端口。
- 不能填 `127.0.0.1`：Dify 运行在服务器容器中，localhost 不指向这台电脑。
- 同一内网仍需验证服务器到电脑的实际通路。

## 3. 先导入连接检查 YAML

将 `dify_http_api_connection_check.yml` 导入为一个独立应用。

在工作流环境变量中设置：

```text
MONGODB_API_BASE_URL（String）：http://电脑IPv4:8000
MONGODB_API_KEY（Secret）：本机API启动时的同一密钥
```

这里不需要 MongoDB 用户名和密码。

密钥若忘记，在原启动窗口先停止 API，用 `$env:MONGODB_API_KEY | Set-Clipboard` 复制，再重新启动 API。

运行连接检查，应输出：

```json
{"ok": true, "http_status": 200, "database": "match_agent"}
```

- 401：API 密钥不一致。
- 503：电脑 API 到 MongoDB 的查询失败，查看 API 终端日志。
- 连接超时/拒绝：检查电脑 IP、监听参数、防火墙及服务器到电脑的网络。
- 403：检查 Dify HTTP 请求实际经过的代理或 SSRF 访问规则，不能只检查 Python 沙箱。

若需开放 Windows 防火墙，请以管理员身份启动 PowerShell，将下面占位符换成实际 Dify 服务器内网 IP，仅允许该来源：

```powershell
New-NetFirewallRule -DisplayName "match-agent API from Dify" -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress "Dify服务器内网IP" -Action Allow
```

不确定服务器出口 IP 时，先由师兄确认，不要直接开放所有来源。

## 4. 再导入完整 HTTP API 工作流

连接检查通过后，将 `formal_selection_quote_v18_1_http_api.yml` 导入为新应用，设置同样的两个环境变量。旧应用继续保留。

检查原有模型和 Excel 插件在目标环境中的配置，仍按原步骤：

```text
上传协议与CAD文字PDF → 开始选型 → 问题确认（如有） → BOM人工确认 → 提交确认BOM → 实时查价 → Excel
```

五处旧产品/价格知识库检索已经替换为 HTTP 调用，不需要绑定旧测试知识库。候选搜索仍只是召回，原有类别/规格/数量/人工确认门禁继续执行；找不到匹配物料时不会凭空生成编码。

由于原50kW资料中使用过临时编码，换正式库后不能以“仍返回原12个编码”作为验收标准，须检查实际匹配和缺项说明，不强行补造匹配。

如果接口返回非200或损坏JSON，流程停止；空产品/价格数组进入原有缺项校验，不用旧知识库兜底。

## 5. 后续改价格会不会影响 API

API 每次请求直接读取 MongoDB，没有价格缓存，YAML 中也不内置单价。因此更新 MongoDB `prices` 中的金额后，下一次查询/报价会使用新金额，不需要重启 API，也不需要再次修改 YAML。

- `material_code` 保持字符串，与产品库对应。
- 保留 `unit_price_ex_tax`、`pricing_unit`、`tax_rate`、`price_version`、`valid_from`、`valid_to`、`status` 等字段及含义。
- 改金额或替换为实际采购价：继续使用原接口。`price_basis` 应随价格来源真实更新，并在报价备注中保留。
- 新旧价格版本同时为 ACTIVE 且有效期重叠：接口会返回两条，工作流会报价格冲突；更新时须明确替换或结束旧版本。
- 停用、过期或删除必填字段会影响报价匹配，这是业务校验，不是 API 服务损坏。
- 已生成的 Excel 不会自动更新；重新提交确认 BOM 后才能重新查价和生成新文件。
- 当前流程不保证一轮多物料报价期间数据库被并发改价时仍是同一价格快照；需要一致性时，应在一次报价结束后切换价格，或后续增加固定价格批次机制。

只有改数据库地址、账号密码、集合名或字段结构时，才可能需要更新 API 配置/代码并重新启动。更换 API 密钥后还需同步更新 Dify Secret。

## 6. 以后部署服务器

API 部署到 Dify 同服务器后，将两个应用中的 `MONGODB_API_BASE_URL` 改成可访问的容器服务地址（例如共享网络上的 `http://match-agent-mongo-api:8000`），密钥保持与服务端一致，不必再次改五处节点。

本地已通过 YAML 解析、全部嵌入代码编译、HTTP节点公开schema校验和离线契约检查。目标Dify环境的网络、模型、插件及完整多轮流程仍需实际运行验收。
