from contextlib import asynccontextmanager
import hashlib
import hmac
from pathlib import Path
import uuid

from fastapi import Depends, FastAPI, File, Form, Header, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse

from .core import Problem, Settings
from .engine import Engine, BUSY


def create_app(settings=None,engine=None):
    settings=settings or Settings()
    # Validate on startup/import so a blank template never exposes an unauthenticated service.
    settings.validate()
    engine=engine or Engine(settings)
    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.close()
    app=FastAPI(title='成套电气项目服务',version='1.0.0',lifespan=lifespan)
    app.state.engine=engine
    def authorized(x_api_key:str=Header(default='')):
        if not hmac.compare_digest(x_api_key,settings.api_key):
            raise Problem('API_KEY_INVALID','项目服务密钥不正确。',401)
    @app.exception_handler(Problem)
    async def problem_handler(request,exc):
        return JSONResponse({'error':exc.code,'message':exc.message},status_code=exc.status)
    @app.get('/live')
    def live():return {'status':'up'}
    @app.get('/ready',dependencies=[Depends(authorized)])
    def ready():
        return {'status':'configured','vision_configured':bool(settings.vision_base and settings.vision_key and settings.vision_model),
            'product_api_configured':bool(settings.product_base and settings.product_key),
            'cad_configured':bool(settings.cad_url),'external_services_verified':False}
    @app.get('/v1/files/{aid}')
    def file(aid:str,token:str=''):
        path=engine.store.resolve_file(aid,token)
        return FileResponse(path,filename=path.name)
    @app.get('/v1/projects/{project_id}',dependencies=[Depends(authorized)])
    def project(project_id:str,actor:str):return engine.describe(engine.authorized_project(project_id,actor))

    def save_upload(file,folder,extension):
        data=file.file.read(settings.max_mb*1024*1024+1)
        if len(data)>settings.max_mb*1024*1024:
            raise Problem('FILE_TOO_LARGE','文件超过配置的大小限制。',413)
        if not data:raise Problem('FILE_EMPTY','上传文件为空。',422)
        if extension=='.pdf' and not data.lstrip().startswith(b'%PDF-'):
            raise Problem('PDF_REQUIRED','图纸与技术协议请导出为PDF后上传。',422)
        path=folder/(uuid.uuid4().hex+extension);path.write_bytes(data);return path

    @app.post('/v1/agent/command',dependencies=[Depends(authorized)])
    def command(operation:str=Form('查看项目'),project_id:str=Form(''),project_name:str=Form('电气成套项目'),
                actor:str=Form(...),notes:str=Form(''),
                drawing_files:list[UploadFile]=File(default=[]),technical_files:list[UploadFile]=File(default=[]),
                confirmation_files:list[UploadFile]=File(default=[]),message_files:list[UploadFile]=File(default=[])):
        if not actor.strip():raise Problem('ACTOR_REQUIRED','缺少Dify用户标识。',422)
        if operation=='新建项目':
            drawing_files=drawing_files+message_files
            if not drawing_files:raise Problem('DRAWING_REQUIRED','请上传图纸PDF；技术协议可选。',422)
            if len(drawing_files)+len(technical_files)>8:raise Problem('TOO_MANY_FILES','单项目最多上传8个PDF。',422)
            p=engine.store.create(project_name or '电气成套项目',actor)
            folder=engine.store.folder(p['id'])
            hashes=set()
            for role,files in [('drawing',drawing_files),('technical_agreement',technical_files)]:
                for upload in files:
                    path=save_upload(upload,folder,'.pdf')
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest in hashes:path.unlink();continue
                    hashes.add(digest)
                    actual_role='technical_agreement' if any(x in (upload.filename or '') for x in ('技术协议','技术要求')) else role
                    p['documents'].append({'document_id':'D'+str(len(p['documents'])+1),'role':actual_role,
                        'filename':Path(upload.filename or 'document.pdf').name,'path':str(path)})
            p['user_notes']=notes[:12000]
            engine.store.save(p,'uploaded',actor)
            return engine.run(p,'recognize')
        p=engine.authorized_project(project_id.strip(),actor)
        if operation=='查看项目':return engine.describe(p)
        if p['state'] in BUSY:return engine.describe(p)
        if operation=='重新识别':
            # New files form a new project to avoid silently changing an approved source revision.
            if drawing_files or technical_files:raise Problem('NEW_PROJECT_REQUIRED','更换图纸请新建项目；重新识别使用已保存资料。',422)
            return engine.run(p,'recognize')
        if operation in ('提交问题确认','提交BOM确认'):
            confirmation_files=confirmation_files+message_files
            if len(confirmation_files)!=1:raise Problem('CONFIRMATION_REQUIRED','请上传一份本项目最新的已填写确认单XLSX。',422)
            path=save_upload(confirmation_files[0],engine.store.folder(p['id']),'.xlsx')
            return engine.run(p,'issues' if operation=='提交问题确认' else 'bom',path)
        if operation=='生成交付文件':return engine.run(p,'generate')
        raise Problem('OPERATION_INVALID','请选择支持的项目操作。',422)
    return app


def main():
    import uvicorn
    import os
    uvicorn.run(create_app(),host=os.getenv('PROJECT_HOST','0.0.0.0'),port=int(os.getenv('PROJECT_PORT','9910')),
                access_log=False)


if __name__=='__main__':main()
