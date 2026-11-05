# PDF 电气识图 V2：模型使用 Dify 已配置的千问

Dify 1.8 中直接选现有 `qwen3-vl-plus`，不需要把千问密钥交给本工具、聊天或本任务环境。

**这份 YML 需要独立 PDF 解析工具。** 工具负责原生文字与坐标、矢量线条、CPU 中文 OCR、局部切图和结果导出；视觉模型由 Dify 调用。现有 PDF-to-Images 插件可以转图，但不承担这里的文字坐标、OCR 和线条关联。新增工具可放独立目录和服务，不覆盖旧 Dify、产品 API、数据同步或数据库。

只接收 PDF，输出 TXT/XLSX/JSON。DWG、选型、报价和生产图纸不在本次范围内。

## 访问口令与模型密钥的区别

`DRAWING_READER_API_KEY` 只是 Dify 访问这个解析工具的口令，用来保护客户图纸，**不是千问密钥**。工具 HTTP 服务不会请求百炼，不需要 `DRAWING_VISION_API_KEY`。其中 `vision.run` 仅是开发时可选的独立调用器，Dify 应用和工具 API 都不调用它。

## 部署和配置

1. 将本目录单独放到服务器，确认 Dify Docker 网络名。默认 `docker_default`，不同时在新目录 `.env` 改为实际值。
2. 在新目录执行以下命令，创建本工具配置。已有 `.env` 时停止，不覆盖。

```bash
python - <<'PY'
from pathlib import Path
import secrets
p=Path('.env')
if p.exists():
    raise SystemExit('.env已存在，不覆盖；请检查已有配置')
p.write_text('DRAWING_READER_API_KEY='+secrets.token_urlsafe(32)+'\nDIFY_NETWORK_NAME=docker_default\n')
print('已创建本工具配置；访问口令不打印')
PY
```

3. 构建并启动这一个工具。首次下载依赖需要联网，本轮尚未验收新镜像的完整构建。

```bash
docker compose -f compose.yml up -d --build
```

4. 查看本工具状态和最近日志。

```bash
docker compose -f compose.yml ps
docker compose -f compose.yml logs --tail 60 electrical-pdf-reader-v2
```

5. 将 `electrical_pdf_reader_native_v2.yml` 导入为新应用，设置应用环境变量：

- `DRAWING_READER_BASE_URL`：`http://electrical-pdf-reader-v2:9912`。
- `DRAWING_READER_API_KEY`：新目录 `.env` 中同名的工具访问口令。

6. 两个视觉节点均选择 Dify 里可调用的 `qwen3-vl-plus`。工作区供应商接入不同则在节点里重新选择已有模型，不需要交出模型密钥。
7. 新开对话，在下方聊天框上传 PDF，发送“开始识图”。每轮最多 6 区，剩余时发送“继续识图”；“查看结果”只下载当前结果。新项目附文件发“新建识图项目”。

工具只加入现有 Dify Docker 网络，不开放宿主机端口。Dify API/worker/代理仍需能够访问该工具地址，按现有网络管理方式配置此服务的访问。不要把工具连接失败当识别成功。

## 验证范围

本地 **56 项检查通过**：四种页面旋转、非连续回路号、同型号不同位置、16A/30mA 分离、正反方向表格、跨区去重、原文与模型分歧、截断 JSON 拒绝、失败保留、PNG 取得、上传及三种附件导出。Dify DSL 本地执行检查使用受控模型回复，只验证编排和接口，不能证明模型准确率。

四份开发样本已跑原生解析/OCR：单线系统图、混合排产图、转曲字体带表格、二次接线图。单线图读取到 8 条支路，三种源文件表格分别读取到 6、9、5 行。这些是开发样本结果，不是未知项目的准确率或全图通过证明；客户资料不包含在公开包内。

**尚未在用户 Dify 中完成真实千问准确率验收。** 需要对照原图逐项核对型号、参数、数量、柜号/回路/相别、连线、端子与跨视图重复。`engineering_verified` 和 `recognition_accuracy_verified` 不会因流程全绿自动变为 true。

- 本地几何关联当前支持带明确标记的水平单线图。复杂多线、二次接线、跨图全拓扑仍需验证。
- 模型读不到但原文存在时保留识别缺项，不擅自生成客户设计问题。
- ATS 后缀 II 不作为 2P 依据；MCB/MCCB 不静默互换；OCR 分数不是准确率。
- 同型号不同位置不合并，重复视图不按出现次数累加为采购数量。
- 模型连线是待核对候选，尚未做完整几何网表复核，不能直接作为逐端子生产接线或批准 BOM。
- 每文件最多 50MiB、40 页，每条消息最多 10 文件。超限明确停止，不截断冒充完整。
- 失败区会保留。API 有 `/v1/projects/{pid}/retry-failed`，归档失败并重开该区；聊天入口暂未接此操作，可新建识图项目重试。

本地复现需 Python 3.12：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest -q test_recognize test_schedules test_vision test_workflow test_api
```

这些检查不请求真实千问，不访问客户数据库。实际 Dify 识图按现有模型账号计费，局部区多时调用量增加。

自有代码按 MIT。PyMuPDF 为 AGPL/商业双许可，OCR、模型和其他组件各按其授权；企业部署需按选用组件处理许可。
