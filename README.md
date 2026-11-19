# 天正电气系统图识别工作台 · 华为云接入版

- [下载 CCE 接口部署包](https://github.com/cola-design/match-agent/raw/refs/heads/tengen-cloud-dify-download-20261119/tengen-cce-dify-gateway.zip)。包含容器构建文件、接口代码和 CCE 部署 YAML。
- [下载 Windows 工作台与云主机接入包](https://github.com/cola-design/match-agent/raw/refs/heads/tengen-cloud-dify-download-20261119/tengen-dify-cloud-workbench-cce.zip)。

CCE 接口部署包内附部署说明。镜像默认监听容器端口 8791。部署配置创建工作负载、Service 和 HTTPS Ingress。Windows 工作台的 GATEWAY_URL 填 Ingress 的 HTTPS 域名。

示例配置使用占位地址，密钥留空。
