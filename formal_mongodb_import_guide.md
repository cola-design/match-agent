# 正式产品数据导入说明

## 推荐导入文件

首批导入产品主数据：

~~~text
products_pending_review.jsonl
~~~

该文件包含全部 116632 条产品记录，记录状态全部是 PENDING_REVIEW。上游 STATUS 字段单独保留，后续状态变化可以通过增量同步更新。

当前上游快照中 STATUS=无效 的记录单独复制在：

~~~text
products_source_invalid_current.jsonl
~~~

该文件包含当前快照中 STATUS=无效 的 47462 条记录。它们可能是资料未补全，后续可能恢复有效，不能当成永久停用记录。

自动发布候选文件：

~~~text
products_auto_active_candidates.jsonl
~~~

该文件包含 54171 条通过 AUTO_RULE_V1 的产品：上游当前有效、描述和类别完整、有标准参数，且没有已识别的 type1/type2 冲突、重复标准参数或缺少规格。导入后这些记录的 data_status 已经是 ACTIVE；其余产品继续使用 products_pending_review.jsonl。

暂时不要导入：

- type2_only_candidates.jsonl：缺少 type1 产品主记录，先人工核对；
- prices_candidates.jsonl：目前只有价格候选结构，没有经过市场核验的单价；
- products_candidates.jsonl：这是全量候选文件，包含有效和无效记录，适合归档。

## 导入前建立索引

在 mongosh 或 Navicat 的 MongoDB 命令窗口执行：

~~~javascript
use match_agent

db.products.createIndex(
  { material_code: 1 },
  { unique: true }
)

db.products.createIndex(
  { data_status: 1, "category.leaf": 1 }
)

db.prices.createIndex(
  { material_code: 1, price_version: 1, valid_from: 1 },
  { unique: true }
)

db.prices.createIndex(
  { material_code: 1, status: 1, valid_from: 1, valid_to: 1 }
)
~~~

## 使用 mongoimport 导入 JSONL

如果使用命令行，产品文件一行就是一条 JSON 文档：

~~~bash
mongoimport --uri "$MONGO_URI" \
  --db match_agent \
  --collection products \
  --file products_pending_review.jsonl \
  --mode upsert \
  --upsertFields material_code
~~~

如果需要导入全量产品主数据，使用 products_pending_review.jsonl。若只想先让 YAML 使用自动通过产品，使用 products_auto_active_candidates.jsonl。两个文件不能同时导入同一 products 集合，否则后一次 upsert 会改变前一次的 data_status。

当前状态筛选副本不需要重复导入：

~~~bash
echo "products_source_invalid_current.jsonl 仅供核对"
~~~

导入后检查：

~~~javascript
db.products.countDocuments({data_status: "PENDING_REVIEW"})
db.products.countDocuments({data_status: "ACTIVE"})
db.products.countDocuments({material_code: {$exists: false}})
~~~

导入自动发布候选后，ACTIVE 数量应为 54171；导入全量待审文件后，ACTIVE 数量应为 0。

## 当前价格文件

prices_candidates.jsonl 包含 69170 条待核价记录。税率暂按 13% 作为默认报价口径，单价和计价单位仍待核验，状态为 PENDING。允许同系列估算，但估算记录必须保留估算标记并经过采购或负责人确认后才能用于正式报价。

## 上游状态字段的重要说明

接口返回的 STATUS=有效/无效 是上游当前快照状态，不是永久生命周期状态。无效可能表示资料尚未补全，后续可能恢复有效；有效也可能后续变为无效。产品记录应保留在 products 中，只更新 source_status。Dify 正式选型同时检查 source_status、人工确认状态和发布状态，不能因为一次无效快照删除产品。

## type1 与 type2 属性处理

products 以 type1 的产品记录为基础，并把同一物料编码下、状态为有效的 type2 属性补充到 source_attributes 和 standard_specs。相同属性和值只保留一份；不同值不覆盖，记录在 type2_audit_issues.jsonl 中。type2 独有记录仍然只作待核查数据，不直接导入 products。

