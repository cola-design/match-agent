from collections.abc import Generator
from typing import Any
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from library.reader import prepare
from tools.common import uploaded_pdf


class PreparePdfTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            data, name = uploaded_pdf(tool_parameters)
            manifest, images = prepare(data, name)
            for filename, blob in images:
                yield self.create_blob_message(blob, meta={'mime_type': 'image/png', 'filename': filename})
            yield self.create_json_message({'success': True, 'manifest': manifest})
        except Exception as exc:
            # No document content, signed file URL, model key or database URI in the message.
            message = str(exc) if isinstance(exc, ValueError) else 'PDF解析失败，请保留本次工具错误记录'
            yield self.create_json_message({'success': False, 'message': message})
            yield self.create_text_message(message)
