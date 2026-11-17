# 天正电气系统图识别工作台 V6.1

本分支仅交付新版前端，不修改 main 分支。

## 下载

- [完整运行包及源码](https://github.com/cola-design/match-agent/raw/refs/heads/tengen-workbench-v6.1-download-20261117/tengen-v6.1.zip)
- [单文件离线预览](https://github.com/cola-design/match-agent/raw/refs/heads/tengen-workbench-v6.1-download-20261117/tengen-v6.1-preview.html)

## 本版调整

- 放大TENGEN和天正电气标识，使中文与英文轮廓高度接近。
- 成套柜选型与报价标题放大并加重。
- 正式工作台移除演示入口，离线HTML明确标为离线预览。
- 检查桌面及手机布局；保留已验证的上传、流式进度、核对页跳转和异常处理。

## 接入

解压后，按包内使用说明将运行版/.env.example复制为.env，填写现有Dify应用的API地址和应用密钥，运行“运行工作台.bat”。已经填写过配置的用户可将旧版运行版/.env复制到新版运行版，关闭旧工作台后再启动新版。

本次公开包不含客户图纸、原YML或真实密钥。正式接入仍需在用户本机与现有Dify、8766解析服务及8774核对服务联调。本地检查使用受控模拟接口，没有调用公司模型或数据库。

ZIP Git blob SHA: d16fc17d02bbda17bf9a311f8a080169ea011ef5
