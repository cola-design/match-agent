# PDF电气识图 V6.1：qwen3-vl-plus版

## 使用

1. 在Dify导入 `pdf_electrical_v6_1_qwen3_vl_plus.yml` 为新应用。
2. 使用Dify中已经配置的通义千问供应商及 `qwen3-vl-plus`；沿用PDF-to-Images插件。
3. 新建对话，上传完整PDF，发送“开始识图”。

三个模型节点均已替换：第一遍回路定位、第二遍器件提取、疑点复看。模型供应商使用之前用户导出流程中的 `langgenius/tongyi/tongyi`。供应商账号和密钥使用各自Dify已有配置，不随YML分发。

模型参数使用通义供应商的 `max_tokens=8192`、`enable_thinking=false`、`response_format=json_object`、`temperature=0`；没有沿用Ollama专用参数。

保留V6.1的逐项兼容校验、可读结果保留、中文解释、回路一致性和诊断输出。此次仅切换模型接入及结果中的模型说明，未改业务提取与校验规则。保留原结果协议标识以便兼容已有结果读取方，实际模型以summary.model和summary.provider为准。

只需导入YML，无需部署脚本或PDF解析服务。通义千问API需要使用者Dify中的有效凭据与调用额度。现有内网数据库、同步服务和产品API均不在此次流程中调用。

代码与结构检查不等于真实识图验收；交付环境未连接用户模型API。需在用户Dify中核对原图的型号、独立参数及回路归属。当前依然使用整页图片与内置提取文字辅助，没有自动局部放大或原生矢量坐标。

`switch_model.py`用于从原V6.1 YML复现本版。用户使用时不需要运行这个脚本。
