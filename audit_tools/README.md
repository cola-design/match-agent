# 现场只读检查

这些检查不更新或删除MongoDB记录。

## API返回格式

保持你电脑上的API运行。把 `audit_running_api.py` 放到API项目文件夹，另开PowerShell，进入该目录执行：

```powershell
.\.venv\Scripts\python.exe .\audit_running_api.py
```

提示API地址时直接回车，默认使用 `http://127.0.0.1:8000`。然后粘贴同一个API密钥，输入时不显示字符。脚本不需要MongoDB密码。

结束后生成 `api_contract_audit_report.json`，可上传这份报告用于核对。不要上传.env或包含密钥的截图。

该脚本是四个核心型号的现场抽查，不代替全库BSON类型统计。

## MongoDB实际字段类型

打开 `check_live_field_types.js`，把其中查询复制到Navicat的match_agent查询窗口运行。结果已展开成普通列，不需打开Array对象。

期望：

```text
material_code_type = string
category_type = object
standard_specs_type = object
source_attributes_type = array
quality_flags_type = array
```

如果出现string类型的category、standard_specs、source_attributes或quality_flags，说明实际导入结构与生成的JSON文件不同。先保留现状和备份，不要直接删除重建整库。
