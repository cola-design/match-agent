from collections.abc import Generator
from typing import Any
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from library.reader import analyze, dumps, merge, prepare, report, strict_json, workbook_bytes
from tools.common import uploaded_pdf


class ExportSelectionTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            data, name = uploaded_pdf(tool_parameters)
            raw = tool_parameters.get('vision_results', '')
            if not isinstance(raw, str) or len(raw) > 60000:
                raise ValueError('视觉结果超过当前整批预算，未截断导出')
            records = strict_json(raw)
            if not isinstance(records, list):
                raise ValueError('视觉结果结构无效')
            native = analyze(data, name)
            manifest, _ = prepare(data, name, render=False, native_data=native)
            context = str(tool_parameters.get('selection_context') or '')[:3000]
            result = merge(native, manifest, records, context)
            workbook = workbook_bytes(result)
            prefix = 'electrical_selection_' + native['source_id']
            yield self.create_blob_message(workbook, meta={
                'mime_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'filename': prefix + '.xlsx'})
            yield self.create_blob_message(dumps(result).encode('utf-8'), meta={
                'mime_type': 'application/json', 'filename': prefix + '.json'})
            yield self.create_json_message({'success': True, 'summary': result['summary']})
            yield self.create_text_message(report(result))
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else '完整结果导出失败，请保留本次工具错误记录'
            yield self.create_json_message({'success': False, 'message': message})
            yield self.create_text_message(message)
