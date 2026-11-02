# 产品查询 API 与自动同步：运行和验收说明（2026-11-02）

当前进度：Windows电脑2026-11-02的全量只读预览及34,901条变更计划已经验收：116,632条产品，340条内容变化，34,561条元信息变化，预计305条ACTIVE退回待审核。尚未收到首次正式写入及写后复核成功的现场报告，尚未在用户服务器部署。

用户已确定长期方案：API在Linux服务器全天运行；产品数据默认每月1日北京时间02:00自动更新，运行窗口02:00—06:00。部署时做一次环境和正式写入验收，后续更新由服务器自动执行，不要求每月手动运行命令。现有API和同步代码已通过隔离测试，可进入服务器部署验收，不能据此声称现场已经上线。

## 本版完成的内容

| 部分 | 行为 |
|---|---|
| API 1.3.0 | 保持现有产品/价格查询地址和数组返回结构；新增 `/ready`、`/v1/status`；精确编码查询发现重复有效记录返回409；厂家缺省显示天正电气，不改写原库 |
| 同步 1.2.0 | 保留末页空对象确认、附件同义比较；新增快速检查、实时进度、退审影响统计、分阶段错误提示、快照摘要和历史快照只读预览 |
| 配置 | 各模块自动读取自己的 `.env`，进程环境变量优先；关闭终端后无需重新输入已保存配置 |
| Linux运行 | API全天后台运行；产品默认每月1日北京时间02:00自动更新；未到期的夜间本地检查不访问源接口或数据库 |

现有产品按用户确认的真实天正电气产品处理。查询服务只读；同步仅更新其负责的来源字段，保留人工字段，不自动发布新增或技术变化的产品，不删除源端本次未返回的产品，不访问或修改价格集合。产品真实性与发布审核状态分别处理。

## 1. 文件结构与升级边界

解压后根目录为 `match_agent_services_20261102`，包含 `mongodb_api/`、`product_sync/`、`deploy/` 和本说明。保留既有 `.env`、数据库和 `sync_state`。包中没有真实凭据、产品快照或客户资料。

本版新增 `product_sync/runtime.py` 和 `python-dotenv` 依赖，不能仅替换 `sync.py`。升级前备份旧代码；使用新目录解压验证，或在停下旧进程后按目录合并代码，保留现场配置、状态卷。不要用旧 ZIP 再覆盖新代码。

无需重建数据库或重新全库清洗；保留的 `repair_products.py` 仅供原库结构确实异常时独立处理，不作为本次升级的必跑步骤。

Windows 可继续复用原 Python。当前运行结束、报告核对后，如需验证此包，在新解压根目录安装同步依赖：

```powershell
& $syncPython -m pip install -r .\product_sync\requirements.txt
```

`start_sync.ps1` 只提供 Preview、Check、Status、Reuse 四个只读模式，没有写入模式。可用 `-PythonPath` 指定原虚拟环境，用 `-StateDir` 指向原安装的绝对 `sync_state` 路径；它不会自动复制或删除旧快照。

## 2. Linux 安装与配置

在具备 Docker Compose 的服务器上解压到固定目录，例如 `/opt/match-agent-services`。下文命令在这个根目录执行。现有 Dify 和 MongoDB继续使用，不由本包重建。

```bash
cd /opt/match-agent-services
cp -n mongodb_api/.env.example mongodb_api/.env
cp -n product_sync/.env.example product_sync/.env
chmod 600 mongodb_api/.env product_sync/.env
nano mongodb_api/.env
nano product_sync/.env
```

API配置：实际存在的 `DIFY_NETWORK`、只读账号 `MONGODB_URI`、数据库名、原查询API的 `MONGODB_API_KEY`。

同步配置：产品源接口的 `PRODUCT_API_KEY`、源地址、独立写入账号 `SYNC_MONGODB_URI`、数据库名、全量起点 `2017-01-01`。先只读预览时也可以使用有读取权限的账号，正式写入前换成专用同步账号。URI保留字符按原成功连接方式编码，值使用单引号。

公开包已移除真实源地址及代码中的默认地址，`PRODUCT_API_URL`必须在本地填写原来成功使用的地址；占位地址会被拒绝。不要从公开示例推测正式地址，也不要将填写后的配置上传仓库。

如果原测试没有手动设置地址，可在本地旧版 `product_sync/sync.py` 中搜索 `PRODUCT_API_URL`，找到当时成功使用的默认地址，填入新版 `.env`。无需公开这个地址。

三种配置不混用：源接口apikey、查询API密钥、MongoDB连接字符串。`MIN_SOURCE_PRODUCTS=116632`沿用当前已知基线；`MAX_CHANGE_RATIO=0.2`是新增和内容变化占比门禁。元信息更新不计为技术变化。`MIN_FREE_DISK_GB=5`是状态卷空闲空间下限，磁盘不足时在源请求前停止，不开始产品写入。

确认服务器能访问正式产品源和MongoDB；Windows电脑能访问不代表服务器或容器也具备相同的VPN/IP白名单条件。Dify网络名用实际已有网络，不直接猜测示例值。

## 3. 构建、启动和检查 API

```bash
sh deploy/services.sh build
sh deploy/services.sh start-api
sh deploy/services.sh api-status
sh deploy/services.sh api-logs
```

现有查询路径继续使用：

```text
GET /v1/products/search?q=型号与规格&model=明确型号&category=类别&limit=30
GET /v1/products/物料编码
GET /v1/prices/物料编码?as_of=YYYY-MM-DD
```

所有查询及下面两项检查使用原 `X-API-Key`，Dify密钥引用方式不变。

| 地址 | 含义 |
|---|---|
| `/live` | 无需密钥，只表示进程在运行 |
| `/health` | 数据库ping可用，保留旧接口 |
| `/ready` | 数据库ping和products/prices实际读取权限可用；容器健康检查使用此项 |
| `/v1/status` | API版本、当前可选产品/待审核/其他状态数量、厂家口径；不是同步任务进度 |

API仍只返回ACTIVE且源端有效的产品。价格按有效日期返回，重叠价格仍交给工作流核对。厂家缺省显示不表示重新清洗或批量改库。损坏的结构字段或数据库故障返回503，不能按“无匹配”处理。

容器默认仅映射服务器 `127.0.0.1:18000`；Dify容器用共享网络中的 `http://match-agent-mongo-api:8000`。跨服务器访问需按现场网络配置HTTPS和定向访问，不将Dify地址设为其自身localhost。

`restart: unless-stopped`使进程退出、Docker重启后恢复；Docker只标记unhealthy时不会自动重启仍存活的进程。管理员需监控健康状态。`.env`改动后用 `docker compose up -d --force-recreate`（在mongodb_api目录）重新创建容器，普通restart不会更新Compose注入的环境。

## 4. 同步：先快速检查，再全量预览

```bash
sh deploy/services.sh check-sync
sh deploy/services.sh check-report
```

快速检查只读取数据库并请求type1/type2各一页；不写产品、不生成完整变更计划，也不证明写入权限或全量完整性。403会说明是源接口拒绝，并记录失败类型/页码/主机，不记录请求密钥或URI。

检查成功后：

```bash
sh deploy/services.sh preview
```

这是新的全量请求。此前现场运行约2小时8分钟，不能据此承诺固定耗时。日志打印页码，`report.json`/`latest.json`持续更新 `fetch_progress`、更新时间和耗时。输入缺失、来源异常、产品/属性覆盖不足、结构或版本冲突时停止，保留运行目录。

长时间交互式预览请使用服务器的tmux/screen等会话保持工具；正式定时运行由systemd管理，不依赖SSH窗口。

## 5. 历史快照只读复核：修改规则后可省去再次取数

```bash
sh deploy/services.sh reuse 20261030T053155Z_3db7c90d
```

运行编号必须属于当前同一状态目录中已经完成的原始全量运行。服务会复制该次 `stage.sqlite` 到新目录，校验报告、摘要/行数、SQLite完整性，再与**此刻数据库**重新比较；生成新的只读计划。不会请求源接口，也不需要源接口密钥，但需要当前数据库读取权限和一致的全量起始日期。

新版本快照校验SHA256；旧版本报告没有摘要时明确标记 `legacy_snapshot_counts_checked`，只完成可取得的结构/计数检查，不能追溯证明旧文件从未被修改。重用的快照仍是旧时点资料；正式写入禁止重用快照，必须重新全量拉取。

Windows原状态目录与服务器Docker卷不是同一位置。上述示例不能直接访问电脑快照；先在电脑使用新包的只读启动器和原状态目录验证，或经现场管理迁移完整运行目录。不要仅复制report或changes文件当作源快照。

## 6. 看报告和决定正式写入

```bash
sh deploy/services.sh report
sh deploy/services.sh status
```

重点检查：`service_version`、`run_id`、`result`、`counts`、`impact.active_to_pending_review`、`impact.planned_quality_flags`、源数量及终止依据。`impact`明确统计本计划中ACTIVE退回待审核、源端变无效、人工保护保留数量。真实现场结果依当时数据为准，11月2日已验收的电脑预览为340条内容变化；服务器执行时仍会重新检查当时的数据。

| 文件 | 更新时机 |
|---|---|
| `latest.json` | 最近一次真正的预览、历史快照预览或正式运行；运行中也更新 |
| `latest_check.json` | 快速检查结果；不会覆盖预览报告 |
| `last_success.json` | 完整正式写入、复核及运行记录保存成功之后 |
| `运行编号/report.json` | 每次独立的完整报告 |

确认完整预览、变更影响、备份与同步账号权限后，人工进行第一次正式执行：

```bash
sh deploy/services.sh apply
sh deploy/services.sh report
sh deploy/services.sh last-success
```

该命令重新获取完整快照，再检查后执行。成功应为 `phase=complete`、`result=success`、`applied_operations=verified_operations`，0条写入也可以成功。分批写入不是全库事务；`products_write_started=true`的失败可能存在已完成批次，保留原文/计划/报告排查，不能自动全库回滚。

`products_write_started=false`的失败表示尚未进入产品写入阶段。源HTTP403/取数失败不会改产品。价格集合始终不写。新增/技术变化产品仍需要审核发布；不能把“真实产品”与“当前已审核可选”合并为同一个状态。

## 7. 一次部署后，每月1日凌晨02:00全自动更新

首次正式写入和写后复核成功后，在完整包根目录执行一次：

```bash
sudo sh deploy/install-timer.sh --period monthly --month-day 1 --start-hour 2 --end-hour 6
sh deploy/services.sh schedule-status
systemctl list-timers match-agent-product-sync.timer
journalctl -u match-agent-product-sync.service -n 100 --no-pager
```

以后无需每月执行apply命令。安装器读取服务器状态卷的 `last_success.json`，确认首次写入及复核成功；将周期、夜间窗口、下次到期日期持久保存到 `schedule.json`，按实际目录和docker路径生成systemd服务。

运行规则：

- 正常每月1日北京时间02:00自动拉取、清洗、对比、受保护写入、复核和保存报告；0条变化同样是成功。
- systemd每天02:00只唤起一个短暂的**本地计划检查**。未到更新日期时不请求产品源、不连接MongoDB、不拉取或写入数据；这不是每天更新产品。
- 如果1日晚服务器关机，恢复时不会白天补跑；会在下一晚02:00执行尚未完成的月度更新。失败保留到期状态，同一夜不重复尝试，下一夜自动重试。
- `Persistent=false`，不使用开机立即执行的规则。到期判断与运行窗口均按北京时间，不依赖宿主机显示时区。
- 02:00—06:00之外不启动自动取数。运行中到截止时间会阻止后续源请求、比较查询和写入批次；systemd超时后停止本任务容器。已发出的请求/批次可能在停止前短暂结束，因此预留06:00至上班前的缓冲。
- 服务与Docker重启不会清空计划；只补一次到期更新，不连补多次历史周期。同一状态卷进程锁避免手动和自动同步重叠。
- API全天提供查询，同步不重启API。源数据变更导致产品退审时，API仍按审核状态过滤；夜间运行不能消除这类业务数据变化。同步分批写入，不是全库原子切换。

可选严格每15天一次（当前默认仍为每月1日）：

```bash
sudo sh deploy/install-timer.sh --period 15days --start-hour 2 --end-hour 6
```

15天模式首次从部署后的下一次夜间窗口开始，此后按成功更新所在夜间的起始日期加15天计算，跨月也保留15天间隔，不用每月1日/16日近似代替。配置相同参数重复安装不会重置已保存的到期日期；改变周期或时段时才重新计算计划。

查看计划和最近检查：

```bash
sh deploy/services.sh schedule-status
docker compose --project-directory product_sync --env-file product_sync/.env -f product_sync/compose.yaml run --rm --entrypoint cat sync /state/schedule_status.json
```

`next_due`是实际下次数据更新日期；systemctl显示的下一次02:00是本地计划检查日期，两者含义不同。月度模式不要按“超过36小时未更新”报异常，结合下次到期、最近结果、失败日志判断。

如有旧timer，先核对其状态；安装器拒绝修改正在运行的systemd同步服务，并在替换计划时停用旧timer。只保留一份调度，不支持不同状态卷或多服务器同时写同一产品库。

停止后续调度：

```bash
sudo systemctl disable --now match-agent-product-sync.timer
```

停止timer不会终止已经开始的同步。需要停止正在执行的任务时，管理员先核对写入阶段，保留该次目录；分批写入可能需要下一夜复核和补齐。

## 8. 运维边界与验证证据

API Docker日志轮转约30MB；同步单个容器日志也轮转，但raw、SQLite及计划持久保留。定期检查磁盘、归档旧运行并保留数据库备份。状态卷不是数据库备份，不运行 `docker compose down -v` 删除它。`--status`显示状态目录空闲空间及最近正式成功距今时间，按 `schedule.json` 的下次更新日期判断是否到期，需要管理员结合计划结果和timer失败日志监控。未接入自动通知，不会自行发送邮件或消息。

本版通过离线单元测试，以及隔离MongoDB7的实际HTTP/API、同步写后复核、认证角色权限测试。验证文件见包内 `validation.json`；没有连接或修改现场MongoDB，没有调用真实产品源，没有在用户服务器启用服务或timer。调度跨日测试使用可控时钟，实际HTTP与MongoDB操作在隔离容器中验证。PowerShell启动器未在Windows系统实际执行，保留原Python路径参数供现场验证。
