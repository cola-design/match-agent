# Dify MongoDB 直连测试

这是一个独立测试工作流，不会修改正式选型报价 Agent。

1. 将 `dify_mongodb_direct_test.yml` 导入 Dify。
2. 运行测试时填写：
   - MongoDB连接字符串：`mongodb://match_agent_api:密码@完整MongoDB地址:27017/?authSource=admin`
   - 数据库名称：`match_agent`
3. 代码节点会返回产品总数、价格总数、ACTIVE 产品数和 ACTIVE 价格数。

预期当前结果：产品总数 116632、价格总数 69170、ACTIVE 产品数 63321、ACTIVE 价格数 69170。

连接字符串只在 Dify 私有测试中填写，不要提交到 GitHub 或聊天记录。若返回 `No module named pymongo`，说明 PyMongo 未安装到真正执行代码的 Dify Sandbox；若返回 `ServerSelectionTimeoutError`，说明 Dify Sandbox 无法访问 MongoDB 内网地址。
