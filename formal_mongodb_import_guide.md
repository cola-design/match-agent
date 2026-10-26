# 正式产品数据导入说明

## 推荐导入文件

首批导入产品主数据：

~~~text
products_pending_review.jsonl
~~~

该文件包含上游状态为“有效”的 69170 条产品记录，记录状态全部是 PENDING_REVIEW。

可选导入：

~~~text
products_inactive.jsonl
~~~

该文件包含上游状态为“无效”的 47462 条记录。导入后保持 INACTIVE，Dify 查询必须过滤掉它们。

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

无效产品需要导入时，再执行：

~~~bash
mongoimport --uri "$MONGO_URI" \
  --db match_agent \
  --collection products \
  --file products_inactive.jsonl \
  --mode upsert \
  --upsertFields material_code
~~~

导入后检查：

~~~javascript
db.products.countDocuments({data_status: "PENDING_REVIEW"})
db.products.countDocuments({data_status: "INACTIVE"})
db.products.countDocuments({data_status: "ACTIVE"})
db.products.countDocuments({material_code: {$exists: false}})
~~~

第一批导入后，ACTIVE 数量应为 0。人工确认并完成工程复核后，再将允许使用的产品改为 ACTIVE。

## 当前价格文件

prices_candidates.jsonl 包含 69170 条待核价记录。税率暂按 13% 作为默认报价口径，单价和计价单位仍待核验，状态为 PENDING。允许同系列估算，但估算记录必须保留估算标记并经过采购或负责人确认后才能用于正式报价。

## 上游状态字段的重要说明

接口返回的 STATUS=有效/无效 是上游当前快照状态，不是永久生命周期状态。无效可能表示资料尚未补全，后续可能恢复有效；有效也可能后续变为无效。产品记录应保留在 products 中，只更新 source_status。Dify 正式选型同时检查 source_status、人工确认状态和发布状态，不能因为一次无效快照删除产品。

