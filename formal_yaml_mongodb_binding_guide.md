# V18 正式 YAML 与 MongoDB 绑定说明

`成套柜智能选型与正式报价_V18.0_正式数据库版.yml` 已按正式 MongoDB 文档结构适配：

- 产品：读取 `material_code`、`description`、`category`、`standard_specs`、`source_status`、`data_status`；只接受 `data_status=ACTIVE` 且 `source_status=有效`。
- 价格：读取 `material_code`、`unit_price_ex_tax`、`pricing_unit`、`tax_rate`、`price_version`、`valid_from`、`valid_to`、`status`、`price_basis`；只接受 `status=ACTIVE` 且报价日期在有效期内。
- 产品和价格通过 `material_code` 精确关联。
- 价格库不要求 `currency` 字段。
- 保留产品规格校验、人工确认、数量口径校验和整批报价阻断逻辑。

## 导入前必须完成的 Dify 配置

当前 YAML 中的 PRODUCT/PRICE 节点是 Dify 的 `knowledge-retrieval` 节点。YAML 本身不会自动打开 MongoDB 网络连接，也不会自动替换知识库 ID。目标 Dify 环境必须先配置能读取 MongoDB `match_agent.products` 和 `match_agent.prices` 的数据源或 MongoDB 查询插件，然后把对应数据源绑定到以下 6 个检索节点：

- PRODUCT：`2000000000022`、`3400000000003`、`3410000000001`、`9167`
- PRICE：`3400000000004`、`3410000000004`、`9207`、`6188`

如果采用 Dify 知识库镜像方式，镜像内容必须保留 MongoDB 字段名，且更新后重新索引；如果采用 MongoDB 查询插件，则应让插件按 `material_code` 查询并返回单条 JSON 文档。

导入 YAML 后，必须检查检索节点的数据源 ID，不能继续绑定旧的测试产品库和测试价格库。
