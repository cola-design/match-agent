# CAD转换与工程图范围

目前没有现成转换接口，因此本包给出了统一接口和可选实现。CAD服务只是把已生成的DXF转换为DWG，不负责从图纸理解工程设计，也不会自动安装CAD软件。

## 标准转换接口

- `POST /convert`
- Header：`X-API-Key: CAD_API_KEY`
- 请求：`multipart/form-data`，字段名`file`，内容为实际DXF文件。
- 成功：HTTP 200，响应体直接是DWG二进制；`Content-Type: application/acad`。
- 失败：HTTP 4xx/5xx，不得用200返回错误页面或把DXF改名DWG。
- 本包不拉取接口返回的任意下载地址。你们后续如果采用异步任务/下载URL接口，应先加经过认证、限定下载来源的适配层，再向项目服务返回DWG二进制。

项目服务`.env`填写完整地址到`CAD_CONVERT_URL`，例如`http://CAD服务地址:9911/convert`；`CAD_CONVERT_API_KEY`与CAD服务配置的`CAD_API_KEY`一致。

## 使用附带的ODA适配实现

先由师兄在服务器安装符合你们许可要求的ODA File Converter，并确认可在该系统的命令行运行。Linux版本可能需要相关共享库/显示运行环境，按官方安装说明处理；包内不包含该软件，也未在本环境执行真实转换。

在本包根目录执行：

```bash
cp cad_service/.env.example cad_service/.env
nano cad_service/.env
python -m uvicorn cad_service.app:app --host 0.0.0.0 --port 9911 --workers 1 --no-access-log
```

前两条复制并填写CAD密钥与`ODA_FILE_CONVERTER_PATH`；最后一条启动转换API。它会调用安装的转换程序，最多等待75秒，返回结果需要有DWG文件头。`GET /live`只检查进程；`GET /ready`需密钥，只检查能否找到转换器，**不代表已实际转换成功**。

不能在转换器未就绪时承诺DWG交付。用你们实际CAD软件打开返回文件，检查单位、文字字体、图层和内容，才完成转换验收。鉴于转换服务可以调用本机程序，按现有服务器制度把它限制在项目后端可访问的范围。

## 工程参数怎么填

BOM表“CAD工程参数”B2保存JSON；黄色格可编辑。初始值来自已识别的实际参数，未知尺寸/坐标为null，不能从PDF像素反推加工尺寸。

- `reviewed`：工程师校核后改为`true`；`reviewer`写姓名。
- `cabinets`：每个柜号必须与确认BOM一致；宽/高/深以毫米填写。
- `layout`：每个实物实例填写`row_id`、`instance`、`x_mm`、`y_mm`、`width_mm`、`height_mm`。坐标从柜体左下角计算。单柜数量3个，必须填写3个实例，不能只画一个。程序会检查实例数量、边界与矩形重叠；不会据此证明电气净距或工艺合格。
- `schematic`：每个接线实例填写row_id、instance和示意坐标，坐标不按实物安装尺寸处理。
- `external_nodes`：外部进出线节点，填写唯一`EXT_`开头id、label、x_mm、y_mm。
- `wires`：`from/to`引用接线节点，填写from_terminal、to_terminal、wire_label、conductor_spec。单柜数量为1的节点名就是row_id；多个实例时使用`row_id@实例序号`，如`I0001@2`。只有一个明确实例可以被画成一个节点。
- `wiring_reviewed`：确认所有接线后设为true。来源未识别清楚的接线必须由工程师补充，不能由程序猜。
- `non_layout_row_ids`与`non_layout_reason`：线材、紧固件等不按独立矩形布局的项，必须明确列出与说明。
- `non_wired_row_ids`：机械件/预留项等没有电气连线的行，明确列出。线材等按米/公斤计量且不作为独立器件画出时，需同时明确排除独立布局与节点。

以下是**通用格式示例，不是你们真实项目的设计数据**，不要照抄尺寸/导线：

```json
{
  "drawing_title": "元件布置与接线关系图",
  "revision": "A",
  "reviewed": true,
  "reviewer": "实际校核人",
  "cabinets": [{
    "cabinet_id": "填写真实柜号",
    "width_mm": 400,
    "height_mm": 600,
    "depth_mm": 200,
    "layout": [{"row_id":"I0001","instance":1,"x_mm":30,"y_mm":100,"width_mm":60,"height_mm":80}],
    "schematic": [{"row_id":"I0001","instance":1,"x_mm":0,"y_mm":100}],
    "external_nodes": [{"id":"EXT_IN","label":"进线","x_mm":0,"y_mm":200}],
    "wires": [{"from":"EXT_IN","to":"I0001","from_terminal":"实际端子号","to_terminal":"实际端子号","wire_label":"实际线号","conductor_spec":"填写已校核的导线规格"}],
    "wiring_reviewed": true,
    "non_layout_row_ids": [],
    "non_layout_reason": "",
    "non_wired_row_ids": []
  }]
}
```

## 尚不能宣称完成的部分

本版DXF绘制柜体外框、元件矩形、实例位号和标注、带端子/线号/导线规格的接线关系节点图。它没有完整的企业电气符号库、柜体结构展开、母排加工图、钣金开孔、复杂控制逻辑与保护配合计算。

最终要形成可排产全套DWG，需要提供并实现企业CAD模板和工艺约束，再按实际项目校核。格式转换服务无法填补这些工程信息。现有业务流程、识别、确认、BOM和文档可先接入，CAD留有明确的工程与转换阻断条件，不会伪造“全套图纸已完成”。
