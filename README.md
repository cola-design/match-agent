# match-agent

当前工作流：[V18.2 HTTP API与动态排版](formal_selection_quote_v18_2_http_api.yml) · [导入与复核步骤](workflow_v18_2_upgrade_guide.md)。API 1.2.0继续使用，无需重跑数据库修复。

现有产品库字段修复：[逐步操作说明](mongodb_api/产品字段修复步骤.md) · [下载本机修复包](mongodb_product_repair_bundle.zip)

最新修复范围与待办：[2026-10-28进展](product_repair_progress_20261028.md)。API 1.2.0；先在本机执行只读预览，不需要重建数据库。

后续产品更新：[定时同步方案与操作步骤](product_sync/README.md) · [下载同步程序](product_sync_bundle.zip)。完整拉取、清洗后按物料编码比较，只写变化项；独立于Dify与只读API运行，价格另行维护。
