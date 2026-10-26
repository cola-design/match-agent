# match-agent MongoDB API

这是给 Dify 调用的只读 API。API 连接 MongoDB `match_agent`，不会把 MongoDB 密码暴露给 Dify。

## 本地运行

```powershell
cd mongodb_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MONGODB_URI='mongodb://用户名:密码@主机:端口/?authSource=admin'
$env:MONGODB_DB='match_agent'
$env:MONGODB_API_KEY='自行生成的接口密钥'
uvicorn app:app --host 0.0.0.0 --port 8000
```

先测试：

```powershell
curl.exe -H "X-API-Key: 自行生成的接口密钥" http://127.0.0.1:8000/health
curl.exe -H "X-API-Key: 自行生成的接口密钥" http://127.0.0.1:8000/v1/products/01010070024
curl.exe -H "X-API-Key: 自行生成的接口密钥" "http://127.0.0.1:8000/v1/prices/01010070024?as_of=2026-10-26"
```

## Dify 配置

将 API 部署到 Dify 能访问的 HTTPS 地址后，在 Dify 添加 HTTP/OpenAPI 工具，导入 `openapi.yaml`，把 `YOUR_API_HOST` 替换为实际地址，在工具请求头中配置 `X-API-Key`。

产品查询使用 `/v1/products/{material_code}`；价格查询使用 `/v1/prices/{material_code}?as_of=报价日期`；候选检索使用 `/v1/products/search?q=检索词&limit=30`。

API 只返回 `data_status=ACTIVE` 且 `source_status=有效` 的产品，以及 `status=ACTIVE` 且在有效期内的价格。
