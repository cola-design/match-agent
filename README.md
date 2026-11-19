# 天正电气系统图识别工作台 · 华为云接入版

- [下载 CCE 兼容接入包](tengen-dify-cloud-workbench-cce.zip)
- [已有安装的 CCE 用户单独下载 server.py](cce/server.py)

CCE 容器需要替换云端 server.py，设置环境变量 `BIND_HOST=0.0.0.0` 和 `PORT=8791` 后重新部署。日志显示 `0.0.0.0:8791` 时，在 CCE 创建 ClusterIP 服务，并通过 HTTPS Ingress 提供工作台访问地址。

云主机直接启动时，默认继续监听 `127.0.0.1:8791`。完整包内有部署与接入说明，示例凭据留空。
