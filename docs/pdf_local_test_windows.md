# 本机 PDF 原生解析测试（Windows PowerShell）

本包先验证原生文字、坐标、支持的水平单线回路与局部切图。仅需 CPU、Python 3.10 以上和两个 Python 库，无需 Docker、服务访问口令或千问密钥。代码与 Dify PDF 解析工具 V2 的原生解析部分相同。

这里默认不启用 OCR，也不调用任何视觉 API。适用于有原生文字的矢量 PDF。扫描件或转曲字体会提示需要 OCR，不会据此声称完整识别。完整服务和 OCR 版仍见原 electrical_pdf_reader_native_v2 目录。

步骤：
1. 解压 ZIP，打开 pdf_reader_local_test 文件夹，应看到 recognize.py。
2. 在资源管理器地址栏输入 powershell，回车，在此目录打开终端。
3. 复制一份要测试的 PDF 到此文件夹，命名为 test.pdf。
4. 运行 python --version 确认 Python 为 3.10 或以上。
5. 创建本目录专用环境：
   python -m venv .venv
6. 安装两个库：
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
7. 解析和切图：
   .\.venv\Scripts\python.exe -u recognize.py ".\test.pdf" --prepare-vision --out ".\test_result_01"

不需要运行 Activate.ps1，所以不用调整 PowerShell 执行策略。
依赖安装需联网，之后这个解析命令不请求远程模型。既有 Python 包和原始 PDF 不会被修改。

输出 test_result_01：
- recognition.xlsx：回路关联、器件标注、文字坐标和待核对项。
- recognition.json：结构化原生解析结果。
- source_with_evidence.pdf：新生成的带证据框 PDF，原文件保留。
- vision：局部图片和 manifest.json，供后续视觉识图。

再次运行请选择新目录，如 --out ".\test_result_02"，避免覆盖已有结果。

已在隔离的 Python 3.12 Linux 环境，仅装 PyMuPDF/openpyxl 及其依赖验证：一个原生文字电梯单线图样本读取到 95 条文字、8 条支路、4 张局部图，模型调用为 0。尚未在实际 Windows 电脑执行验收；Windows 步骤调用相同 Python 入口。

边界：
- 解析成功不代表千问已看懂图、全部符号/端子连接正确，也不是已批准采购清单。
- 当前几何关联主要支持带明确回路标记的水平单线图。
- PDF 高分辨率切图可保留细节；原始扫描件本身模糊时，解析不能补回缺失内容。
- 若服务器 Dify 要调用电脑上的工具，需要两者网络可达。Dify 中的 localhost/127.0.0.1 指向 Dify 所在环境，不能用来指代用户电脑。本次先本机解析，不要求配置网络入口。
- 不要使用 --vision；那个可选开发开关会调用外部视觉 API，本次测试不需要它。

自有代码见 LICENSE。PyMuPDF 采用 AGPL/商业双许可，其他依赖各按自身许可。
