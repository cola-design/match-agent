"""Local PDF evidence service. All cloud model calls stay inside Dify."""
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

import fitz
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook

from recognize import recognize, write_reports
from vision import prepare, validate, collect

MODEL_ORIGIN = "dify-model-node"
MODEL_RECORD = "Dify configured model; check the actual node execution"
RUN_ID = hashlib.sha256((MODEL_ORIGIN+"\n"+MODEL_RECORD).encode()).hexdigest()[:12]


def dump(path, data):
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(path)


def create_app(root=None, api_key=None, ocr_mode="auto"):
    root=Path(root or os.getenv("DRAWING_DATA_DIR","./drawing_data")).resolve()
    root.mkdir(parents=True,exist_ok=True)
    key=api_key if api_key is not None else os.getenv("DRAWING_READER_API_KEY","")
    locks={};global_lock=threading.RLock()
    api=FastAPI(title="PDF electrical evidence service",version="2.0.0")

    def auth(x_api_key:str=Header(default="")):
        if not key or key=="replace-with-a-long-random-key":raise HTTPException(503,"请设置解析工具访问口令DRAWING_READER_API_KEY；无需模型密钥")
        if not hmac.compare_digest(x_api_key,key):raise HTTPException(401,"解析工具访问口令不匹配")

    @contextmanager
    def locked(pid):
        with global_lock:lock=locks.setdefault(pid,threading.RLock())
        with lock:yield

    def folder(pid):
        if not re.fullmatch(r"[a-f0-9]{32}",pid):raise HTTPException(404,"项目不存在")
        p=root/pid
        if not (p/"project.json").is_file():raise HTTPException(404,"项目不存在")
        return p

    def project(pid):return json.loads((folder(pid)/"project.json").read_text())
    def manifest(p,sid):return json.loads((p/"sources"/sid/"vision"/"manifest.json").read_text())
    def response_path(p,sid,uid):return p/"sources"/sid/"vision"/"responses"/RUN_ID/(uid+".json")

    def get_unit(pid,uid):
        p=folder(pid)
        if not re.fullmatch(r"[a-f0-9]{32}-p\d+-region\d+",uid):raise HTTPException(404,"分区不存在")
        sid=uid.split("-",1)[0]
        if sid not in {s["source_id"] for s in project(pid)["sources"]}:raise HTTPException(404,"来源不存在")
        m=manifest(p,sid);u=next((x for x in m["units"] if x["unit_id"]==uid),None)
        if u is None:raise HTTPException(404,"分区不存在")
        return p,sid,u

    @api.get("/live")
    def live():return {"status":"up","version":"2.0.0","api_key_configured":bool(key),"cloud_model_calls":0}

    @api.post("/v1/projects",dependencies=[Depends(auth)])
    def new_project(body:dict):
        pid=uuid.uuid4().hex;p=root/pid;p.mkdir()
        dump(p/"project.json",{"project_id":pid,"name":str(body.get("name") or "PDF电气识图")[:100],"sources":[],"failed_sources":[]})
        return {"project_id":pid}

    @api.post("/v1/projects/{pid}/sources",dependencies=[Depends(auth)])
    def upload(pid:str,file:UploadFile=File(...)):
        p=folder(pid);name=Path(str(file.filename or "drawing.pdf").replace("\\","/")).name
        name=re.sub(r"[^\w.()#~ -]","_",name)[:150]
        if not name.lower().endswith(".pdf"):raise HTTPException(400,"本阶段只接收PDF")
        with locked(pid):
            sid=uuid.uuid4().hex;dest=p/"sources"/sid;dest.mkdir(parents=True)
            original=dest/name
            try:
                size=0;sha=hashlib.sha256()
                with original.open("wb") as out:
                    while chunk:=file.file.read(1024*1024):
                        size+=len(chunk)
                        if size>50*1024*1024:raise HTTPException(413,"PDF超过50MiB，请拆分后提交")
                        sha.update(chunk);out.write(chunk)
                state=project(pid)
                for old in state["sources"]:
                    if old["sha256"]==sha.hexdigest():
                        shutil.rmtree(dest);return dict(old,duplicate_upload=True)
                with fitz.open(original) as doc:
                    maxpages=int(os.getenv("DRAWING_MAX_PAGES","40"))
                    if not 0<len(doc)<=maxpages:raise ValueError(f"每文件允许1至{maxpages}页，超出请拆分；不会截取部分页冒充完整")
                native=recognize(original,ocr_mode=ocr_mode,ocr_engine="rapidocr")
                write_reports(native,original,dest)
                m=prepare(original,native,dest/"vision")
                for u in m["units"]:
                    u["unit_id"]=sid+"-"+u["unit_id"];u["context"]["unit_id"]=u["unit_id"]
                dump(dest/"vision"/"manifest.json",m)
                entry={"source_id":sid,"filename":name,"sha256":sha.hexdigest(),"pages":len(native["pages"]),"units":len(m["units"])}
                state["sources"].append(entry)
                state["failed_sources"]=[s for s in state["failed_sources"] if s["filename"]!=name]
                dump(p/"project.json",state)
                return entry
            except Exception as exc:
                state=project(pid)
                state["failed_sources"]=[s for s in state["failed_sources"] if s["filename"]!=name]
                state["failed_sources"].append({"filename":name,"error_type":type(exc).__name__})
                dump(p/"project.json",state);shutil.rmtree(dest,ignore_errors=True)
                if isinstance(exc,HTTPException):raise
                raise HTTPException(422,"PDF解析未完成："+str(exc)[:250]) from None

    @api.get("/v1/projects/{pid}/batch",dependencies=[Depends(auth)])
    def batch(pid:str,limit:int=6):
        if not 1<=limit<=12:raise HTTPException(400,"每轮允许1至12区")
        with locked(pid):
            p=folder(pid);state=project(pid);todo=[];total=done=0
            for source in state["sources"]:
                sid=source["source_id"]
                for u in manifest(p,sid)["units"]:
                    total+=1
                    if response_path(p,sid,u["unit_id"]).is_file():done+=1
                    elif len(todo)<limit:todo.append(u["context"])
            return {"batch":todo,"total_units":total,"completed_units":done,"failed_sources":state["failed_sources"]}

    @api.get("/v1/projects/{pid}/units/{uid}/image",dependencies=[Depends(auth)])
    def image(pid:str,uid:str):
        p,sid,u=get_unit(pid,uid)
        return Response((p/"sources"/sid/"vision"/u["image"]).read_bytes(),media_type="image/png",
                        headers={"Content-Disposition":'inline; filename="drawing-region.png"'})

    @api.post("/v1/projects/{pid}/results",dependencies=[Depends(auth)])
    def save_result(pid:str,body:dict):
        uid=str(body.get("unit_id") or "");p,sid,u=get_unit(pid,uid)
        if body.get("failed") is True:
            data={"status":"failed","unit_id":uid,"model":MODEL_RECORD,"error_type":"two_invalid_model_outputs"}
        else:
            raw=body.get("raw")
            if not isinstance(raw,str) or len(raw.encode())>120000:raise HTTPException(422,"模型文本缺失或超出分区预算")
            try:data={"status":"response_validated","raw":raw,"model":MODEL_RECORD,**validate(raw,u)}
            except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)[:250]) from None
        with locked(pid):
            target=response_path(p,sid,uid);target.parent.mkdir(parents=True,exist_ok=True);dump(target,data)
        return {"saved":True,"unit_id":uid,"needs_review":True,"engineering_verified":False}

    @api.post("/v1/projects/{pid}/retry-failed",dependencies=[Depends(auth)])
    def retry_failed(pid:str):
        count=0
        with locked(pid):
            p=folder(pid)
            for source in project(pid)["sources"]:
                sid=source["source_id"]
                for u in manifest(p,sid)["units"]:
                    f=response_path(p,sid,u["unit_id"])
                    if f.is_file() and json.loads(f.read_text()).get("status")!="response_validated":
                        archive=f.with_name(f.stem+"-failed-"+uuid.uuid4().hex[:8]+".json");f.replace(archive);count+=1
        return {"retry_units":count}

    def report(pid):
        p=folder(pid);state=project(pid);sources=[];total=done=failed=unbound=gaps=conflicts=0
        wb=Workbook();wb.remove(wb.active)
        for source in state["sources"]:
            sid=source["source_id"];dest=p/"sources"/sid;m=manifest(p,sid)
            collect(m,dest/"vision",MODEL_ORIGIN,MODEL_RECORD)
            result=json.loads((dest/"recognition_with_vision.json").read_text());v=result["vision"]
            total+=len(m["units"]);failed+=sum(x["status"]=="failed" for x in v["failed_or_unprocessed_units"])
            done+=len(m["units"])-sum(x["status"]=="not_processed" for x in v["failed_or_unprocessed_units"])
            unbound+=len(v["unassociated_native_device_markings"]);gaps+=len(v["recognition_gaps"]);conflicts+=len(v["conflicts"])
            sources.append(dict(source,extraction=result))
            original=load_workbook(dest/"recognition_with_vision.xlsx",data_only=False)
            for sheet in original:
                if sheet.title not in wb.sheetnames:wb.create_sheet(sheet.title).append(["来源文件"]+[c.value for c in sheet[1]])
                for row in sheet.iter_rows(min_row=2):wb[sheet.title].append([source["filename"]]+[c.value for c in row])
            original.close()
        summary={"sources":len(sources),"total_units":total,"completed_units":done,"pending_units":total-done,"failed_units":failed,
                 "unassociated_native_labels":unbound,"model_reported_gaps":gaps,"model_native_conflicts":conflicts,
                 "processing_complete":bool(total) and done==total and failed==0 and not state["failed_sources"],
                 "recognition_accuracy_verified":False,"engineering_verified":False,"parser_cloud_model_calls":0}
        result={"project_id":pid,"summary":summary,"sources":sources,"failed_sources":state["failed_sources"],
                "scope":"仅PDF识图；模型由Dify调用，真实模型信息请查Dify节点运行记录。未连接产品库、价格库或绘图服务。"}
        dump(p/"extraction.json",result)
        ws=wb.create_sheet("识别进度");ws.append(["字段","值"])
        for k,v in summary.items():ws.append([k,str(v)])
        for sheet in wb:
            sheet.freeze_panes="A2";sheet.auto_filter.ref=sheet.dimensions
            for column in sheet.columns:sheet.column_dimensions[column[0].column_letter].width=29
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value,str):cell.data_type="s"
        wb.save(p/"extraction.xlsx")
        text=[result["scope"],json.dumps(summary,ensure_ascii=False,indent=2)]
        for source in sources:
            text.append("\n文件："+source["filename"])
            for page in source["extraction"]["native_extraction"]["pages"]:
                text.extend(f"第{page['page']}页 | {a['raw']} | {a['meaning']} | {a['id']}" for a in page["annotations"])
            text.append(json.dumps(source["extraction"]["vision"],ensure_ascii=False,indent=2))
        (p/"extraction.txt").write_text("\n".join(text),encoding="utf-8")
        return result

    @api.get("/v1/projects/{pid}/report",dependencies=[Depends(auth)])
    def get_report(pid:str):
        with locked(pid):r=report(pid)
        s=r["summary"]
        return {"summary":s,"message":f"已处理{s['completed_units']}/{s['total_units']}区，失败{s['failed_units']}区。格式校验通过不代表识图准确率已验收。"}

    @api.get("/v1/projects/{pid}/files/{name}",dependencies=[Depends(auth)])
    def files(pid:str,name:str):
        types={"extraction.json":"application/json","extraction.xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","extraction.txt":"text/plain; charset=utf-8"}
        if name not in types:raise HTTPException(404,"文件不存在")
        with locked(pid):report(pid);data=(folder(pid)/name).read_bytes()
        return Response(data,media_type=types[name],headers={"Content-Disposition":f'attachment; filename="{name}"'})

    return api


app=create_app()
