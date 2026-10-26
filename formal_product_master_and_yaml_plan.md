# 正式产品主数据与 YAML 泛化改造方案

## 总体原则

产品库是正式主数据，价格库是独立的价格主数据，YAML 是调用和编排流程。数据库字段不为了适配当前 YAML 临时改变，YAML 应通过稳定的数据查询契约访问数据库。

正式流程：

~~~text
接口原始数据
→ 清洗候选数据
→ 人工确认
→ ACTIVE 发布
→ 产品选型
→ 价格匹配
→ 人工 BOM 确认
→ 报价
~~~

原始 JSON、清洗日志和问题清单保留在文件归档中，正式 MongoDB 只保存可查询的产品和价格记录。

## 产品库 products

产品库一条记录对应一个物料编码：

~~~json
{
  "material_code": "01050080377",
  "item_id": "123456",
  "description": "产品描述",
  "category": {
    "code": "330.33002.3300256",
    "name": "配电断路器类.塑壳断路器.TGM1N",
    "path": ["配电断路器类", "塑壳断路器", "TGM1N"],
    "leaf": "TGM1N"
  },
  "standard_specs": {
    "model": {
      "value_text": "TGM1N",
      "raw_name": "型号说明",
      "source": "type1"
    },
    "rated_current_a": {
      "value_text": "40A",
      "value_num": 40,
      "unit": "A",
      "raw_name": "额定电流",
      "source": "type1"
    }
  },
  "source_attributes": [
    {
      "name": "额定电流",
      "value": "40A",
      "source": "type1"
    }
  ],
  "source_status": "有效",
  "data_status": "PENDING_REVIEW",
  "join_status": "TYPE1_AND_TYPE2_MATCHED",
  "quality_flags": [],
  "last_update_date": "2026-10-25 10:00:00.0",
  "provenance": {
    "source": "product-spec-api",
    "date_from": "2017-01-01",
    "type1_page": 1
  }
}
~~~

字段规则：

- material_code、item_id 永远按字符串保存；
- source_attributes 保存接口原始属性，不能因为标准映射失败而丢弃；
- standard_specs 只保存已确认含义的参数，供选型查询使用；
- 属性值保留原文，同时在可以确定时补充数值和单位；
- 重复属性和 type1/type2 冲突保留在 quality_flags；
- PENDING_REVIEW 不参与正式 ACTIVE 选型；
- INACTIVE 不参与选型；
- 只有人工确认后的记录才能改为 ACTIVE。

## type1/type2 关系

type=1 作为产品主记录来源，type=2 作为属性补充和校验来源。

允许的关联状态：

- TYPE1_AND_TYPE2_MATCHED：存在有效 type2 属性；
- TYPE1_AND_TYPE2_INVALID_ONLY：只找到无效 type2 属性；
- TYPE1_ONLY：没有可用 type2 属性；
- TYPE2_ONLY：只有 type2，缺少 type1 主记录；
- CONFLICT：两种来源的同名属性值不一致。

type2 独有记录先进入待核查文件，不直接发布为 ACTIVE 产品。

## 价格库 prices

价格库一条记录对应一个物料、一个价格版本和一个有效期：

~~~json
{
  "material_code": "01050080377",
  "unit_price_ex_tax": 28500.00,
  "pricing_unit": "台",
  "tax_rate": 0.13,
  "price_version": "CN-MARKET-2026-V1",
  "valid_from": "2026-10-25",
  "valid_to": "2027-04-22",
  "status": "PENDING",
  "source": "中国市场参考价"
}
~~~

价格规则：

- 没有精确型号、计价单位和可靠市场依据时不填价格；
- PENDING 价格不能参与正式报价；
- 普通标准电气元件参考有效期 180 天；
- 铜、铝、电缆、柜体等价格波动较大的产品参考有效期 90 天；
- 厂家正式价目表有明确有效期时，以厂家有效期为准；
- 新价格生成新版本，不覆盖历史记录；
- 价格状态由 PENDING 经采购或负责人确认后才改为 ACTIVE。

当前接口只提供产品规格，不提供价格，因此不能从接口直接生成正式价格金额。

## 当前正式数据清洗结果

当前正式 API 压缩包已完成完整性检查：

- type1：116632 条；
- type2：884476 条；
- type1 有效：69170 条；
- type1 无效：47462 条；
- 有效 type1/type2 可关联产品：9153 条；
- type2 独有待核查产品：7514 条；
- 清洗问题：9887 条。

已生成的候选文件：

- products_candidates.jsonl：全部产品候选；
- products_pending_review.jsonl：上游有效、等待人工确认的产品；
- products_inactive.jsonl：上游无效产品；
- type2_only_candidates.jsonl：type2 独有记录；
- issues.jsonl：重复参数、属性冲突和缺失规格；
- prices_candidates.jsonl：价格待核工作表。

## YAML 改造边界

当前 YAML 中的测试逻辑需要迁移：

- 删除正式流程中的 TMP-CN 和 TEST_ONLY 发布路径；
- 产品查询改为调用标准产品查询契约；
- 价格查询改为按 material_code、status、valid_from、valid_to 查询；
- 产品状态、价格状态和人工确认状态分别判断；
- 选型只允许使用 ACTIVE 产品；
- 报价只允许使用 ACTIVE 价格；
- 缺价、冲突、无效产品和待确认产品必须进入阻断或待人工处理；
- YAML 只保留流程规则，不再解析旧 Excel 字段；
- 参数别名和单位映射独立维护，不写死在大量代码节点中。

## 发布门禁

正式报价必须同时满足：

1. 产品记录存在且 data_status=ACTIVE；
2. 产品编码与人工确认 BOM 一致；
3. 产品类别和关键参数校验通过；
4. 价格记录存在且 status=ACTIVE；
5. 价格有效期覆盖报价日期；
6. 计价单位与 BOM 单位一致；
7. 数量、税率、版本和来源完整；
8. 所有冲突和待确认项已经处理。

在以上条件完成前，系统可以输出候选和问题清单，但不应输出正式工程 BOM 或正式对外报价。

## 上游状态的生命周期说明

接口的 STATUS=有效/无效 是当前同步快照，不是永久停用标记。无效记录可能因为资料未补全而后续恢复有效，有效记录也可能后续变为无效。正式 products 集合保留完整记录，source_status 随增量同步更新；选型发布同时检查 source_status、人工确认和发布状态。
