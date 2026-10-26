# ACTIVE 产品分片导入与发布

`products_auto_active_json_parts.zip` 包含 63,321 条自动规则通过的产品记录，分为 4 个 JSON 数组文件。

## 1. 导入临时集合

解压后，在 Navicat 中对 `match_agent` 数据库执行“导入向导”，一次选择一个文件：

- 目标集合：`products_active_candidates`
- 导入方式：添加
- 文件类型：JSON，编码 UTF-8
- 不要导入到 `products`

## 2. 合并到正式产品集合

四个分片全部导入临时集合后，在查询窗口执行：

```javascript
db.products_active_candidates.aggregate([
  { $project: { _id: 0 } },
  {
    $merge: {
      into: "products",
      on: "material_code",
      whenMatched: "merge",
      whenNotMatched: "discard"
    }
  }
])
```

## 3. 验证

```javascript
db.products.countDocuments({data_status: "ACTIVE"})
db.products.countDocuments({data_status: "PENDING_REVIEW"})
```

预期分别为 `63321` 和 `53311`。确认后可删除临时集合：

```javascript
db.products_active_candidates.drop()
```
