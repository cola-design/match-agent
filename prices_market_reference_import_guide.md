# 正式价格库导入说明

`prices_market_reference_parts.zip` 包含 69,170 条价格记录，分为 4 个 Navicat 可直接导入的 JSON 数组文件。

## 价格字段

- `material_code`：与 `products.material_code` 一一对应
- `unit_price_ex_tax`：未税单价（CNY）
- `pricing_unit`：计价单位，当前统一为 `件`
- `tax_rate`：0.13
- `currency`：CNY
- `price_version`：CN-MARKET-2026-V1
- `valid_from`：2026-10-26
- `valid_to`：2027-10-25
- `status`：ACTIVE
- `price_basis`：MARKET_REFERENCE_ESTIMATE
- `confidence`：MEDIUM/LOW

价格金额按中国市场同品类参考水平、产品系列和规格参数生成。后续取得真实采购价后，使用相同 `material_code` 更新 `unit_price_ex_tax`、版本和有效期即可，不需要修改产品库结构。

## 导入步骤

1. 在 `match_agent` 数据库中新建普通集合：`prices`。
2. 解压压缩包。
3. 右键 `prices` → 导入向导，每次只选一个文件：`prices_market_part_01.json` 到 `prices_market_part_04.json`。
4. 选择 JSON、UTF-8、根标签 `(Root)`、导入模式“添加”，目标集合为已有 `prices`。
5. 四个文件全部导入后执行：

```javascript
db.prices.countDocuments({})
db.prices.countDocuments({status: "ACTIVE"})
```

两个结果都应为 `69170`。
