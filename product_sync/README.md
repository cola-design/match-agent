# 产品接口定时同步：全量拉取、差异写入

这是一项独立的数据更新任务，配合只读查询API使用，不在Dify代码节点中运行。当前字段修复完成并验收后，才运行此任务。先预览，后启用写入和定时器。

## 1. 这项任务做什么

```text
正式产品接口（新旧混合，type=1产品＋type=2属性）
  → 分页拉取并保存压缩原文
  → SQLite暂存、编码校验、重复与分页异常检查
  → 清洗、合并规格、保留冲突标记
  → 按物料编码与MongoDB逐项比较
  → 保存修改前后计划
  → 只写新增或变化的产品
  → 写后校验、生成报告

Dify → 只读查询API → MongoDB中当前可发布产品和有效价格
```

第一版每次使用已确认的全量起始日期，拉完整个范围。即使接口返回新旧混合，也通过内容比较筛出变化；不把最后一次运行日期或最大的更新时间当作唯一筛选条件。它是“全量拉取、增量写入”，不是声称接口只返回增量。

默认建议每天北京时间02:00运行一次。拉取频率、分页请求间隔需根据接口方限额及现场耗时调整。现有数据约117页产品、885页属性，实际页数随数据变化，不写死。每种类型继续拉到明确空页，分页失败不开始更新。

## 2. 如何识别与处理变更

| 比较结果 | 操作 |
|---|---|
| 编码不存在 | 新增产品，PENDING_REVIEW，不自动变成正式选型产品 |
| 编码存在，实际内容和版本相同 | 不写入 |
| 型号描述、分类、规格、来源状态等变了 | 更新来源字段；此前ACTIVE的产品退回PENDING_REVIEW；保留人工停用状态 |
| 只有更新时间变新，实际内容相同 | 更新版本信息，保留原发布状态 |
| 上游版本比现有记录旧，或无法证明不是旧版本 | 保留现有记录，写问题清单 |
| 本次没有返回某个产品 | 保留，不删除、不擅自停用，写未返回清单 |
| SOURCE STATUS由有效变无效 | 保留产品，更新快照状态；API不会再将它作为有效候选 |
| 无效以后恢复有效 | 再次同步，但需按待审核状态确认后才发布 |
| manual_override=true，或有人工补充的来源属性 | 跳过自动覆盖，进入问题清单，保留人工内容 |

比较使用稳定的内容摘要：忽略分页行号、拉取时间和属性顺序；更新时间用于识别倒退。不会用“时间变了”直接认定技术参数变了。

如果接口同时返回同一编码的新旧版本，有可靠且不相同的LAST_UPDATE_DATE时选择较新版本，与页序无关；同一时间却内容不同、或缺少可比较时间时停止本次写入，不能随便取最后一条。属性版本只有物料编码、ATTR_ID、SA_ID能明确关联时才这样归并；缺少稳定标识的属性保留冲突供核对。

程序只拥有 products 的列明来源字段，使用 `$set`，不整体替换文档，保留其他人工字段。它会更新产品的发布状态以要求重新审核，但不会把所有产品统一发布为ACTIVE。物料编码保持字符串，不能补猜丢失的前导零。

**不访问、不生成、不改写 prices。** 现有接口没有价格、税率和计价单位，不能用它更新真实价格。真实价格以后通过独立价格来源、版本和有效期规则更新。新产品缺价时，报价流程应明确阻断。

## 3. 权限与部署关系

- 查询API：继续用只读 `match_agent_api`。
- 同步任务：用独立 `match_agent_sync` 写入账号。所需权限仅为 products 的 find/insert/update/createIndex/listIndexes，以及 product_sync_runs 的 insert；不需要价格库写权限或删除权限。
- Dify：继续调用查询API，不保存上游接口密钥和MongoDB写入账号。

不要把正在运行API的数据库账号改成写入账号。同步服务无HTTP端口，不需要接入Dify网络；它只需能访问正式接口和MongoDB。独立Compose项目不会重启或修改Dify。

## 4. 本机先预览

下载包：<https://github.com/cola-design/match-agent/raw/refs/heads/main/product_sync_bundle.zip>

解压后进入 `product_sync_bundle` 根目录，其中包含 product_sync 和 mongodb_api 两个子目录。这里的mongodb_api仅提供共享字段处理文件，不需要启动另一个API。

如希望复用之前API的虚拟环境，在PowerShell先设置它的完整路径（不激活虚拟环境）：

```powershell
$syncPython = 'C:\Users\dongyuan1\Desktop\mongodb_api_server_bundle\mongodb_api\.venv\Scripts\python.exe'
```

在本机隐藏输入接口密钥：

```powershell
$sourceKey = Read-Host '请输入正式产品接口apikey' -AsSecureString
$env:PRODUCT_API_KEY = [System.Net.NetworkCredential]::new('', $sourceKey).Password
```

同样隐藏输入MongoDB完整连接字符串。预览可用只读账号；只有正式写入需要独立写入账号。连接字符串中的保留字符必须URL编码：

```powershell
$syncUri = Read-Host '请输入MongoDB连接字符串' -AsSecureString
$env:SYNC_MONGODB_URI = [System.Net.NetworkCredential]::new('', $syncUri).Password
$env:MONGODB_DB = 'match_agent'
$env:SOURCE_DATE_FROM = '2017-01-01'
```

起始日期必须覆盖完整产品与属性范围，并以接口方确认为准。2017-01-01沿用此前取数范围，不表示接口方已承诺这个日期之后一定包含全量。

确认当前PowerShell所在目录就是解压的 product_sync_bundle，再运行：

```powershell
& $syncPython -m product_sync.sync
```

默认只读预览。不要在当前产品字段修复仍在运行时执行同步。预览结束后，先检查 `sync_state\latest.json` 及本次目录中的报告和问题清单。不要把密钥或原始数据库URI发到聊天中。

当前产品数少于预期、属性快照异常、重复冲突、分页重复、原库结构类型错误，会停止。默认变化超过原库20%时，允许生成预览，但禁止正式写入。阈值调整必须有预览依据，不要只为让任务成功而关闭校验。

### 已保存页的本地诊断

如果预览报告为 `SOURCE_MATERIAL_CODE_NOT_NONEMPTY_STRING`，表示源记录的 ITEM_NUM 缺失、不是字符串、为空或含内部空白。这不是 MongoDB 密码错误。程序已在校验前保存原始响应，不用重新拉一遍数据即可定位。

旧包用户先在包含 product_sync 和 mongodb_api 的根目录下载单文件：

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/cola-design/match-agent/main/product_sync/diagnose_snapshot.py" -OutFile ".\product_sync\diagnose_snapshot.py"
.\.venv\Scripts\python.exe .\product_sync\diagnose_snapshot.py
Copy-Item .\sync_state\source_diagnostic.json "$env:USERPROFILE\Desktop\sync_source_diagnostic.json"
```

诊断默认读取 latest.json 指向的运行；可用 `--run-id 运行编号` 指定旧运行。它只查看该次运行每种类型最后保存的一页，输出准确页码、行号、编码实际类型、编码值及少量记录标识，不读取接口密钥、不请求网络、不访问数据库，也不修改原运行报告。先核对诊断报告再决定源数据处理策略；不要擅自补前导零或静默跳过不完整产品快照后写库。

### 2026-10-30：末页单个空对象的兼容修正

收到的现场诊断显示，type1 第118页的 `data` 只有一个空对象 `[{}]`，没有任何字段；这条占位记录触发了原来的编码校验。诊断没有检查前117页，不能仅凭该报告确认全量完整，也不能证明接口方正式承诺了此终止格式。

新版 `source.py` 仅在上一页有记录且不足 `page_size` 条、当前页恰好为 `[{}]` 时，再请求并保存下一页。下一页也必须是 `[]` 或 `[{}]` 才结束该类型分页，并将终止页、上一页条数及确认页写入报告的 `snapshot.type1.termination` / `snapshot.type2.termination`。这是一项带额外检查的兼容处理，不能替代源接口的稳定分页与快照契约。

首屏或整页之后出现 `[{}]`、空对象与产品混合、有其他字段但缺编码、数字编码、确认页出现数据或请求失败，都会停止。不会把空对象作为产品入库，也不会跳过真实异常产品。原有产品数/属性数基线及写入前检查继续生效。

现有安装只需备份并替换 `product_sync/source.py`。在原同步程序根目录、沿用原Python及凭据配置，重新执行不带 `--apply` 的预览。此次会重新拉取完整快照，旧 `sync_state` 运行目录保留。预览完成后核对 `latest.json`，先不要启用正式写入或定时器。

## 5. 服务器独立部署（本机预览通过后）

推荐固定目录 `/opt/match-agent-sync`，将解压后两个子目录和根目录 `.dockerignore` 一起放到这里：

```text
/opt/match-agent-sync/
├── .dockerignore
├── mongodb_api/product_contract.py
└── product_sync/
    ├── sync.py
    ├── cleaning.py
    ├── source.py
    ├── compose.yaml
    ├── Dockerfile
    ├── .env.example
    └── deploy/
```

由有服务器操作权限的人在该目录执行：

```bash
cd /opt/match-agent-sync/product_sync
cp .env.example .env
chmod 600 .env
nano .env
```

填写正式PRODUCT_API_KEY、独立写入账号的SYNC_MONGODB_URI、正确的数据库、全量起点及记录数基线。不要上传真实.env。MIN_SOURCE_PRODUCTS示例116632来自本次现场总数，后续需根据已确认的源端范围维护。

构建、检查配置、只读预览：

```bash
docker compose config --quiet
docker compose build
docker compose run --rm sync
```

预览、权限和报告都核对后，首次正式执行：

```bash
docker compose run --rm sync --apply
```

正常报告为 `result=success`、`phase=complete`，applied_operations和verified_operations一致。若成功写入0条且unchanged数量符合实际，同样是成功，而不是没有运行。原API无需因产品内容变化而重启。

新建唯一物料编码索引可能被已有重复记录、既有索引定义或权限不足阻断，此时停止并处理原因，脚本不会删除旧索引。首次正式运行前应做好数据库备份。

## 6. 启用每天02:00的定时任务

完成首次正式运行并核对结果后，才安装systemd模板。先执行 `command -v docker`；模板ExecStart使用 `/usr/bin/docker`，如果实际路径不同，修改模板中的路径。确认服务器使用支持Docker Compose的docker命令。

模板默认工作目录 `/opt/match-agent-sync/product_sync`，如果换目录，需要同时改service。

```bash
sudo cp deploy/match-agent-product-sync.service /etc/systemd/system/
sudo cp deploy/match-agent-product-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now match-agent-product-sync.timer
systemctl list-timers match-agent-product-sync.timer
```

时间表达式明确使用Asia/Shanghai，不依赖服务器当前时区。Persistent=true表示错过计划时在恢复后补跑一次。已运行的同一systemd服务不会被重复启动；同一state卷的手动任务也由进程锁保护。这里不支持跨服务器同时写同一产品库；多机运行需另加分布式锁或单一调度器。

查看结果：

```bash
systemctl status match-agent-product-sync.service
journalctl -u match-agent-product-sync.service -n 100 --no-pager
docker compose run --rm --entrypoint python sync -c 'from pathlib import Path; print(Path("/state/latest.json").read_text())'
```

取出最后一次报告到当前目录，便于上传核对：

```bash
docker compose run --rm --entrypoint cat sync /state/latest.json > sync_latest_report.json
```

停止后续定时运行：`sudo systemctl disable --now match-agent-product-sync.timer`。这不会自动终止已开始的任务。不要在任务运行时执行Docker卷删除操作。

## 7. 可追溯性、故障与审核

每次运行在持久卷单独目录保存：

- `report.json`：运行模式、阶段、计数、失败类别、耗时起止。
- `raw/type1`、`raw/type2`：逐页压缩JSON原文，不含请求头或密钥。
- `stage.sqlite`：本次暂存和去重数据。
- `changes.jsonl.gz`：修改前后计划。用于审计和人工恢复依据，不可直接交给旧repair_products.py回滚，它们格式不同。
- `issues.jsonl`：跳过、冲突、缺失参数等问题。
- `not_returned_by_source.jsonl`：未返回但已保留的产品。

成功运行另写一条product_sync_runs记录。失败或部分执行以本地持久卷报告为准，systemd服务返回失败。每页网络/429/部分5xx错误最多重试3次，认证和业务失败不盲目重试。失败不会推进日期游标；本版每次重新全量拉取，再次执行通过内容比较补齐，不累加重复产品。

分批写入不是全库事务：写入或并发保护失败时，可能已有前面批次完成，脚本不会强行覆盖人工修改，也不会自动全库回滚。保留本次原文和计划，排查后重跑。若修改期间人工确认某个产品，写入原始字段保护会阻止覆盖该确认状态。

新增、改变技术内容的产品进入PENDING_REVIEW后，需由产品维护人员核对类别、参数、冲突、来源状态和配套价格再发布。发布流程要检查该产品当前sync_meta.content_hash，避免审核旧版本时误批准新版本。

日志会轮转，但快照和计划不会自动清理。服务器落地前须配置磁盘监控、数据库备份及保留周期；不能把这个卷当作数据库备份。通知渠道尚未接入，管理员需监控systemd失败和latest.json的完成时间；未得到明确授权不会自动发送邮件或消息。

## 8. 验证范围与上线前确认

已通过17项同步规则检查，以及隔离MongoDB7的真实HTTP拉取→暂存→比较→写入→复核：混合新旧数据只更新变化项，重复执行0写入，未返回产品保留，拉取中途业务失败无产品写入，人工字段保留，并发人工保护生效，价格记录不变。

另用原始上传product-data.rar完成全量离线暂存验证：117页、116632条产品；885页、884476条属性。此验证针对原文读取、分页、编码与暂存，不等于已在现场完成全量更新。

尚未拿正式密钥在用户服务器上验证，也没有给真实数据源发请求。上线还需确认：接口的全量起点、分页终止行为、稳定排序/快照能力、type2版本语义、限流及实际网络权限。此接口目前没有已知快照token或可靠删除事件；源端翻页期间发生变更时，不能保证得到事务一致快照。重复页/冲突检测和每日全量对账可以降低遗漏风险，不能替代接口方的稳定快照承诺。

后续若启用真正的日期增量请求，应先确认dateFrom对type1/type2的过滤语义，再增加回看窗口、仅在完整成功后推进的游标和周期性全量对账；不能直接把当前日期存起来当作后续唯一边界。
