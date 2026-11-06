from dify_plugin.file.file import File
from library.reader import MAX_BYTES


def uploaded_pdf(parameters):
    files = parameters.get('pdf_files')
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError('请只上传一份完整PDF')
    file = files[0]
    if isinstance(file, dict):
        file = File(**file)
    name = str(file.filename or 'drawing.pdf').replace('\\', '/').rsplit('/', 1)[-1]
    if not name.lower().endswith('.pdf'):
        raise ValueError('本流程直接处理PDF，不能把DWG改扩展名作为PDF上传')
    if file.size and file.size > MAX_BYTES:
        raise ValueError('PDF超过30MB')
    try:
        data = file.blob  # Official Dify file transport; no replacement hosts or arbitrary fetches.
    except Exception as exc:
        raise ValueError('无法读取Dify上传文件，请核对现有Dify文件访问配置') from exc
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError('PDF读取失败或超过30MB')
    return data, name
