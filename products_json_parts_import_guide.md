# products_pending_review 分片导入说明

**2026-10-28补充：不能只用条数验收。** 已在实际导入库中发现Navicat把嵌套对象/数组存成JSON字符串。已导入的数据不要再次插入或删库；按 [产品字段修复步骤](mongodb_api/产品字段修复步骤.md) 先只读预览。后续导入必须核对实际BSON类型：material_code为string、category和standard_specs为object、source_attributes和quality_flags为array。检查脚本见 [check_live_field_types.js](audit_tools/check_live_field_types.js)。

`products_pending_review_json_parts.zip` 包含 6 个标准 JSON 数组文件，共 116,632 条产品记录。

1. 下载并解压 `products_pending_review_json_parts.zip`。
2. 在 Navicat 中打开 `match_agent` 数据库，右键 `products`，选择“导入向导”。
3. 每次选择一个 `products_pending_review_part_XX.json` 文件。
4. 文件类型选 JSON；导入方式选“插入”；编码选 UTF-8。
5. 依次导入 01 到 06，不能重复导入同一个分片。
6. 导入后执行：

```javascript
db.products.countDocuments({})
```

结果应为 `116632`。

这些文件与原始 `products_pending_review.jsonl` 内容一致，只是将每 20,000 条记录包装成了 Navicat 可以识别的 JSON 数组；最后一片为 16,632 条。
