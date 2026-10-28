# 正式产品数据接口获取与清洗操作指导

**后续自动更新请改用 [产品定时同步](product_sync/README.md)。** 本文描述首次手工取数；不能把历史导入命令直接当作定时覆盖任务。新同步程序先拉完整快照、校验、比较，再写差异，不删除本次未返回的产品，不修改价格。

## 1. 目的

本指导用于从正式产品规格接口获取产品主数据，保存原始 JSON，执行清洗，并生成供选型报价 Agent 和 MongoDB `products` 集合使用的候选数据。

执行顺序固定为：

```text
正式接口 → 原始 JSON 备份 → 数据清洗 → 问题清单 → 人工确认 → MongoDB → YAML/Dify 回归验证
```

不要把接口返回结果先复制到 Excel。物料编码可能有前导零，Excel 转换可能造成编码和原始规格属性丢失。

## 2. 接口信息

接口地址：

```text
http://api2.tengen.com.cn/env-101/otherportal/otherproject/ai/getProductSpec
```

请求方式：`GET`

请求头：

```text
Accept: application/json
apikey: 接口方提供的真实密钥
```

请求参数：

| 参数 | 类型 | 说明 | 首次建议 |
|---|---|---|---|
| `dateFrom` | string | 增量起始日期，格式 `yyyy-MM-dd` | 使用接口方确认的全量起始日期 |
| `pageNum` | int | 页码，从 1 开始 | 脚本自动处理 |
| `pageSize` | int | 每页数量 | `1000` |
| `type` | int | 数据形态 | 首次使用 `1` |

`type=1` 是产品级数据，一条物料一条记录，作为正式 `products` 的主来源。`type=2` 是属性级明细，后续用于核对，不要和 `type=1` 作为两批产品重复导入。

文档中的 `2017-01-01`、`2026-10-01` 都是调用示例。正式全量同步日期必须由接口提供方确认。

## 3. 使用前准备

### 3.1 获取正式密钥

向接口提供方确认：

1. 正式接口地址是否仍然有效；
2. 正式 `apikey`；
3. 首次全量同步使用的 `dateFrom`；
4. 是否必须连接公司 VPN 或内网；
5. 是否可以提供 HTTPS 地址。

文档中已经出现过示例密钥。不要继续使用该示例值，应让接口方重新生成正式密钥。不要把真实密钥提交到 Git、写入脚本或发送到聊天中。

### 3.2 准备脚本

本指导使用的脚本是：

```text
/workspace/project-review/api_to_mongodb.py
```

如果在本地电脑执行，需要把该脚本和项目目录复制到本地；如果在 Codex 工作区执行，直接进入 `/workspace/project-review`。

## 4. Windows PowerShell 操作步骤

### 4.1 进入脚本目录

把路径替换为实际目录：

```powershell
cd C:\你的路径\project-review
```

### 4.2 设置接口密钥

```powershell
$env:PRODUCT_API_KEY = "接口方提供的真实apikey"
```

该变量只在当前终端窗口有效。不要把这行保存到脚本、Git 或截图中。

### 4.3 拉取正式产品数据

把 `dateFrom` 换成接口方确认的日期：

```powershell
python .\api_to_mongodb.py fetch `
  --url "http://api2.tengen.com.cn/env-101/otherportal/otherproject/ai/getProductSpec" `
  --date-from "2017-01-01" `
  --type 1 `
  --page-size 1000 `
  --out-dir "api_import"
```

脚本会自动：

- 从第 1 页开始请求；
- 自动递增 `pageNum`；
- 每页保存原始 JSON；
- 当前页少于 1000 条时停止；
- 如果当前页正好 1000 条，继续请求下一页；
- 记录拉取日期、接口地址、数据类型和总行数。

### 4.4 检查原始数据

原始文件位置：

```text
api_import\raw\type1\page-000001.json
api_import\raw\type1\page-000002.json
...
```

打开任意一页，确认结构包含：

```json
{
  "status": true,
  "message": "查询成功",
  "data": []
}
```

如果 `status` 不是 `true`，先停止，不要清洗或导入。

### 4.5 执行清洗

```powershell
python .\api_to_mongodb.py clean `
  --type 1 `
  --out-dir "api_import"
```

清洗结果：

```text
api_import\products_type1.jsonl
api_import\issues_type1.jsonl
api_import\clean_summary_type1.json
```

其中：

- `products_type1.jsonl`：清洗后的候选产品记录；
- `issues_type1.jsonl`：缺编码、缺类别、规格格式异常、重复属性等问题；
- `clean_summary_type1.json`：记录总数和问题数量。

所有首次清洗结果的 `data_status` 都是 `PENDING_REVIEW`，人工确认后才能发布为 `ACTIVE`。

## 5. Linux 或 macOS 操作步骤

```bash
cd /workspace/project-review
export PRODUCT_API_KEY='接口方提供的真实apikey'

python api_to_mongodb.py fetch \
  --url 'http://api2.tengen.com.cn/env-101/otherportal/otherproject/ai/getProductSpec' \
  --date-from '2017-01-01' \
  --type 1 \
  --page-size 1000 \
  --out-dir api_import

python api_to_mongodb.py clean \
  --type 1 \
  --out-dir api_import
```

## 6. 如何判断是否成功

拉取成功的表现：

- 终端显示多页 `page=... rows=... saved=...`；
- `api_import/raw/type1/` 下存在多个分页 JSON；
- 每页 JSON 的 `status` 为 `true`；
- 最后一页数量小于 `pageSize`，或下一页为空。

清洗成功的表现：

- 生成 `products_type1.jsonl`；
- 生成 `issues_type1.jsonl`；
- 产品记录的 `material_code` 保持字符串；
- 产品记录的 `data_status` 为 `PENDING_REVIEW`；
- 重复规格属性没有被静默覆盖，而是进入问题清单。

## 7. 常见错误

### HTTP 403

通常表示 `apikey` 缺失、错误、过期或没有权限。联系接口提供方重新确认密钥和访问权限。

### 网络连接失败

确认电脑是否连接公司 VPN 或内网，并确认接口地址是否要求 HTTPS。

### `status=false`

表示接口业务层返回失败。保留响应文件，把错误消息交给接口提供方，不要继续导入。

### 只有一页数据

检查 `dateFrom` 是否过晚，检查是否把 `pageNum` 固定成了 1。使用本指导脚本时不需要手动修改页码。

### 产品有编码但没有规格

保留记录，进入 `issues_type1.jsonl`，不能从产品描述或型号名称猜填工程参数。

## 8. 交付给数据清洗人员的文件

完成拉取后，把以下内容压缩成一个 ZIP 上传：

```text
api_import/
├── fetch_manifest.json
└── raw/
    └── type1/
        ├── page-000001.json
        ├── page-000002.json
        └── ...
```

同时说明：

- 使用的 `dateFrom`；
- 总页数；
- 是否通过 VPN 或内网访问；
- 是否所有页面 `status=true`；
- 是否还拉取了 `type=2`。

不要上传 `PRODUCT_API_KEY`、MongoDB 密码或包含密钥的终端截图。

## 9. 后续处理边界

拿到原始 JSON 后，清洗工作包括：

1. 校验物料编码唯一性并保持为字符串；
2. 解析 `CATEGORY_NAME` 和 `CATEGORY_CODE`；
3. 解析 `SPEC_STRING`；
4. 把规格属性映射为 YAML 使用的标准参数；
5. 生成缺失项、重复项和冲突项清单；
6. 用 50kW 测试用例回归验证；
7. 根据真实字段完善 YAML；
8. 人工确认后导入 MongoDB `products` 集合。

该接口文档没有价格、税率、计价单位、价格版本和有效期字段，所以不能仅凭此接口建立正式 `prices` 价格库。

