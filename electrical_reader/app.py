"""A bounded drawing parser used by Dify HTTP nodes. No model or product DB calls."""
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reader import dump, model_context, parse_source, render, safe_name
from domain import VERSION, decode, normalized, parameter_text, CATEGORY_NAMES

PROJECT_LOCKS={}
LOCKS_LOCK=threading.Lock()


def lock_for(pid):
    with LOCKS_LOCK:return PROJECT_LOCKS.setdefault(pid,threading.RLock())


def finite_box(box):
    return isinstance(box,list) and len(box)==4 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and 0<=x<=1 for x in box) and box[0]<box[2] and box[1]<box[3]


def validate_result(context, raw):
    if len(raw.encode())>60000:raise ValueError('分区模型输出过大，未采用截断内容')
    def no_duplicates(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise ValueError('JSON重复字段')
            d[k]=v
        return d
    text=raw.strip()
    if text.startswith('```'):
        match=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',text)
        if not match:raise ValueError('JSON代码块不完整')
        text=match.group(1)
    data=json.loads(text,object_pairs_hook=no_duplicates,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('非有限数')))
    if not isinstance(data,dict) or not isinstance(data.get('items'),list) or not isinstance(data.get('issues'),list):raise ValueError('缺少items或issues数组')
    if data.get('unit_id')!=context['unit_id'] or not isinstance(data.get('page_understood'),bool):raise ValueError('分区归属或page_understood无效')
    if len(data['items'])>80:raise ValueError('分区超过80项，需要细分图面')
    if context['kind']=='overview' and data['items']:raise ValueError('概览仅输出图面/柜体索引；元件交给细分区提取，避免重复')
    anchors={a['anchor_id']:a for a in context['anchors']}
    for item in data['items']:
        if not isinstance(item,dict) or not isinstance(item.get('raw_label'),str) or not finite_box(item.get('bbox')):raise ValueError('元件必须有原文及有效图片位置')
        ids=item.get('anchor_ids',[])
        if not isinstance(ids,list) or any(not isinstance(x,str) or x not in anchors for x in ids):raise ValueError('引用了不属于本区的原文编号')
        quantity=item.get('quantity')
        if quantity is not None and (isinstance(quantity,bool) or not isinstance(quantity,(int,float)) or not math.isfinite(quantity) or quantity<=0):raise ValueError('数量应为实际正数或null')
        item.setdefault('uncertainties',[])
        if not isinstance(item['uncertainties'],list):raise ValueError('uncertainties应为数组')
        if ids:
            quoted=' '.join(anchors[x]['raw'] for x in ids)
            comparable=lambda s:re.sub(r'\s+','',normalized(s)).upper()
            if comparable(item['raw_label']) not in comparable(quoted):
                raise ValueError('原文标注与所引用的原生文字不一致；不得凭推断替换MCB/MCCB等缩写')
        else:item['uncertainties'].append('由视觉读取，未关联原生文字证据，需对照原图核对')
        item['decoded']=decode(item['raw_label'])
        item['interpretation_status']='ai_extracted_not_engineer_verified'
    for field in ('drawings','cabinets','notes'):
        if field in data and not isinstance(data[field],list):raise ValueError(field+'应为数组')
    return data


def create_app(root=None, api_key=None):
    root=Path(root or os.getenv('DRAWING_DATA_DIR','./drawing_data')).resolve();root.mkdir(parents=True,exist_ok=True)
    key=api_key if api_key is not None else os.getenv('DRAWING_READER_API_KEY','')
    api=FastAPI(title='Electrical drawing reader',version=VERSION)
    def auth(x_api_key:str=Header(default='')):
        if not key or key=='replace-with-a-long-random-key':raise HTTPException(503,'请先配置解析工具的DRAWING_READER_API_KEY')
        if not hmac.compare_digest(x_api_key,key):raise HTTPException(401,'解析工具密钥不匹配')
    def folder(pid):
        if not re.fullmatch('[a-f0-9]{32}',pid):raise HTTPException(404,'项目不存在')
        p=root/pid
        if not (p/'project.json').is_file():raise HTTPException(404,'项目不存在')
        return p
    def load_project(pid):return json.loads((folder(pid)/'project.json').read_text())
    def source_for(p,sid):
        if not re.fullmatch('[a-f0-9]{32}',sid):raise HTTPException(404,'来源不存在')
        f=p/'sources'/sid/'source.json'
        if not f.is_file():raise HTTPException(404,'来源不存在')
        return json.loads(f.read_text())
    @api.get('/live')
    def live():return {'status':'up','version':VERSION,'api_key_configured':bool(key)}
    @api.post('/v1/projects',dependencies=[Depends(auth)])
    def new_project(body:dict):
        pid=uuid.uuid4().hex;p=root/pid;p.mkdir()
        name=str(body.get('name') or '电气图纸识别')[:100]
        dump(p/'project.json',{'project_id':pid,'name':name,'sources':[],'failed_sources':[]})
        return {'project_id':pid}
    @api.post('/v1/projects/{pid}/sources',dependencies=[Depends(auth)])
    def upload(pid:str,file:UploadFile=File(...)):
        p=folder(pid);name=safe_name(file.filename);ext=Path(name).suffix.lower()
        if ext not in ('.pdf','.dwg','.dxf'):raise HTTPException(400,'仅接收PDF、DWG或DXF')
        with lock_for(pid):
            sid=uuid.uuid4().hex;where=p/'sources'/sid;where.mkdir(parents=True)
            original=where/name
            size=0
            with original.open('wb') as out:
                while chunk:=file.file.read(1024*1024):
                    size+=len(chunk)
                    if size>50*1024*1024:
                        original.unlink(missing_ok=True);raise HTTPException(413,'单文件超过50MiB，请分批提交')
                    out.write(chunk)
            sha=hashlib.sha256(original.read_bytes()).hexdigest();project=load_project(pid)
            for old in project['sources']:
                if old['sha256']==sha:return dict(old,duplicate_upload=True)
            try:
                source=parse_source(original,where,sid)
            except Exception as exc:
                project['failed_sources'].append({'filename':name,'error':str(exc)[:300]});dump(p/'project.json',project)
                raise HTTPException(422,'图纸解析失败：'+str(exc)[:300]) from None
            entry={'source_id':sid,'filename':name,'sha256':sha,'pages':len(source['pages']),'units':len(source['units'])}
            project['sources'].append(entry)
            project['failed_sources']=[r for r in project['failed_sources'] if r['filename']!=name]
            dump(p/'project.json',project)
            return entry
    @api.get('/v1/projects/{pid}/batch',dependencies=[Depends(auth)])
    def batch(pid:str,limit:int=6):
        if not 1<=limit<=12:raise HTTPException(400,'每轮分区数量应在1至12之间')
        p=folder(pid);project=load_project(pid);pending=[];total=0
        for entry in project['sources']:
            source=source_for(p,entry['source_id'])
            for u in source['units']:
                total+=1
                if not (p/'results'/(u['unit_id']+'.json')).exists() and len(pending)<limit:
                    try:pending.append(model_context(source,u))
                    except ValueError as exc:raise HTTPException(422,str(exc)) from None
        done=len(list((p/'results').glob('*.json'))) if (p/'results').exists() else 0
        return {'project_id':pid,'total_units':total,'completed_units':done,'batch':pending,'failed_sources':project['failed_sources']}
    @api.get('/v1/projects/{pid}/units/{uid}/image',dependencies=[Depends(auth)])
    def image(pid:str,uid:str):
        p=folder(pid)
        if not re.fullmatch(r'[a-f0-9]{32}-p\d+-u\d+',uid):raise HTTPException(404,'分区不存在')
        source=source_for(p,uid.split('-')[0])
        if not any(u['unit_id']==uid for u in source['units']):raise HTTPException(404,'分区不存在')
        return Response(render(source,uid),media_type='image/png',headers={'Content-Disposition':'inline; filename="drawing-region.png"'})
    @api.post('/v1/projects/{pid}/results',dependencies=[Depends(auth)])
    def save_result(pid:str,body:dict):
        p=folder(pid);uid=str(body.get('unit_id') or '')
        if not re.fullmatch(r'[a-f0-9]{32}-p\d+-u\d+',uid):raise HTTPException(400,'分区编号无效')
        source=source_for(p,uid.split('-')[0]);u=next((u for u in source['units'] if u['unit_id']==uid),None)
        if u is None:raise HTTPException(400,'分区不属于本来源')
        try:
            if body.get('failed') is True:
                data={'unit_id':uid,'page_understood':False,'items':[],'issues':['模型两次未能输出可校验结果，该分区仍需识别或人工核对']}
            else:data=validate_result(model_context(source,u),str(body.get('raw') or ''))
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc)) from None
        data.update(source_id=source['source_id'],filename=source['filename'],page=u['page'],kind=u['kind'],bbox_page=u['bbox_page'])
        with lock_for(pid):
            (p/'results').mkdir(exist_ok=True)
            dump(p/'results'/(uid+'.json'),data)
        return {'saved':True,'unit_id':uid,'needs_review':not data['page_understood'] or bool(data['issues'])}
    def report(pid):
        p=folder(pid);project=load_project(pid);anchors=[];items=[];issues=[];drawings=[];cabinets=[];notes=[];seen={};total=0;pending=0;failed=0
        for failure in project['failed_sources']:issues.append({'type':'source_failed',**failure})
        for e in project['sources']:
            source=source_for(p,e['source_id']);total+=len(source['units'])
            for warning in source['warnings']:issues.append({'type':'source_warning','filename':e['filename'],'message':warning})
            for page in source['pages']:
                for a in page['anchors']:anchors.append(dict(a,filename=e['filename'],page=page['page']))
            for u in source['units']:
                f=p/'results'/(u['unit_id']+'.json')
                if not f.exists():pending+=1;continue
                data=json.loads(f.read_text());drawings.extend({'source_id':e['source_id'],'page':u['page'],'data':x} for x in data.get('drawings',[]))
                cabinets.extend({'source_id':e['source_id'],'page':u['page'],'data':x,'status':'AI待核对'} for x in data.get('cabinets',[]))
                notes.extend({'source_id':e['source_id'],'page':u['page'],'data':x,'status':'AI待核对'} for x in data.get('notes',[]))
                if not data['page_understood']:failed+=1
                issues.extend({'type':'model_issue','filename':e['filename'],'page':u['page'],'message':str(q)} for q in data['issues'])
                for row in data['items']:
                    item=dict(row,source_id=e['source_id'],filename=e['filename'],page=u['page'],unit_id=u['unit_id'])
                    ids=tuple(sorted(item.get('anchor_ids') or []))
                    identity=(e['source_id'],u['page'],ids,normalized(row['raw_label'])) if ids else None
                    if identity and identity in seen:
                        old=items[seen[identity]]
                        for field in ('cabinet_id','circuit_no','phase','quantity'):
                            if old.get(field)!=item.get(field):issues.append({'type':'overlap_disagreement','anchor_ids':list(ids),'field':field,'message':'重叠分区对同一标注的归属/数量判断不一致，需核对原图'})
                        old.setdefault('also_seen_in',[]).append(u['unit_id']);continue
                    if identity:seen[identity]=len(items)
                    items.append(item)
        used={aid for row in items for aid in row.get('anchor_ids',[])}
        candidates=[a for a in anchors if a['decoded']['category']!='unclassified']
        missing=[a for a in candidates if a['anchor_id'] not in used]
        for a in missing:issues.append({'type':'native_label_not_associated','filename':a['filename'],'page':a['page'],'anchor_id':a['anchor_id'],'raw':a['raw'],'message':'原生标注已经读取，但尚未与视觉识别元件/回路关联；不得据此宣称完整识别'})
        summary={'sources':len(project['sources']),'total_units':total,'completed_units':total-pending,'pending_units':pending,'failed_units':failed,
                 'native_text_records':len(anchors),'visual_records':len(items),'unassociated_native_labels':len(missing),
                 'processing_complete':bool(total) and pending==0 and not project['failed_sources'], 'engineering_verified':False,
                 'recognition_status':'needs_review' if failed or missing or issues or pending else 'ai_extracted_pending_engineer_review'}
        result={'version':VERSION,'project_id':pid,'name':project['name'],'summary':summary,'native_annotations':anchors,
                'visual_extractions':items,'drawing_index':drawings,'cabinet_index':cabinets,'notes':notes,'issues':issues,
                'scope':'仅图纸信息提取和常用标注解释；原文记录数不等于实物数量。视觉模型输出尚未经工程师确认。未连接产品库、价格库或绘图接口。'}
        text=[project['name'],json.dumps(summary,ensure_ascii=False),result['scope'],'','原生文字及含义（保留未分类原文）']
        for a in anchors:text.append(f"{a['filename']} 第{a['page']}页 | {a['raw']} | {a['decoded']['meaning']} | {parameter_text(a['decoded']['parameters'])}")
        text+=['','图面、柜体及技术说明（AI待核对）']+[json.dumps(x,ensure_ascii=False) for x in drawings+cabinets+notes]
        text+=['','视觉提取（AI待核对）']+[json.dumps(x,ensure_ascii=False) for x in items]+['','待核对问题']+[json.dumps(x,ensure_ascii=False) for x in issues]
        wb=Workbook();ws=wb.active;ws.title='原文与含义'
        ws.append(['文件','页码','原图标注','类别','常用含义','参数','需确认内容','原图位置','原文编号'])
        for a in anchors:ws.append([a['filename'],a['page'],a['raw'],CATEGORY_NAMES[a['decoded']['category']],a['decoded']['meaning'],parameter_text(a['decoded']['parameters']),'；'.join(a['decoded']['uncertainties']),json.dumps(a['bbox_page']),a['anchor_id']])
        ws=wb.create_sheet('视觉提取待核对');ws.append(['文件','页码','柜号','回路','相别','原文','元件类型','数量（AI待核）','视觉解释','原文引用','待确认'])
        for x in items:ws.append([x['filename'],x['page'],str(x.get('cabinet_id') or ''),str(x.get('circuit_no') or ''),str(x.get('phase') or ''),x['raw_label'],str(x.get('kind') or ''),str(x.get('quantity') if x.get('quantity') is not None else '待确认'),str(x.get('meaning') or ''),json.dumps(x.get('anchor_ids',[])),json.dumps(x.get('uncertainties',[]),ensure_ascii=False)])
        ws=wb.create_sheet('待核对');ws.append(['问题类型','详细内容'])
        for x in issues:ws.append([x['type'],json.dumps(x,ensure_ascii=False)])
        ws=wb.create_sheet('识别进度');ws.append(['字段','值'])
        for k,v in summary.items():ws.append([k,str(v)])
        for ws in wb:
            ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
            for row in ws:
                for cell in row:
                    if isinstance(cell.value,str):cell.data_type='s'
                    cell.alignment=Alignment(vertical='top',wrap_text=True)
            for column in ws.columns:ws.column_dimensions[column[0].column_letter].width=28
            for cell in ws[1]:cell.fill=PatternFill('solid',fgColor='234F6B');cell.font=Font(color='FFFFFF',bold=True)
        for col,width in {'A':30,'B':8,'C':50,'D':22,'E':45,'F':70,'G':65,'H':35}.items():wb['原文与含义'].column_dimensions[col].width=width
        wb['原文与含义'].column_dimensions['I'].hidden=True
        buf=io.BytesIO();wb.save(buf)
        return result,'\n'.join(text),buf.getvalue()
    @api.get('/v1/projects/{pid}/report',dependencies=[Depends(auth)])
    def get_report(pid:str):
        result,_,_=report(pid)
        return {'project_id':pid,'summary':result['summary'],'message':f"已处理 {result['summary']['completed_units']}/{result['summary']['total_units']} 个图纸分区；剩余 {result['summary']['pending_units']} 个。结果含原文、含义、视觉归属与待核对项。"}
    @api.get('/v1/projects/{pid}/files/{name}',dependencies=[Depends(auth)])
    def files(pid:str,name:str):
        if name not in ('extraction.txt','extraction.json','extraction.xlsx'):raise HTTPException(404,'文件不存在')
        result,text,xlsx=report(pid)
        content={'extraction.txt':text.encode(),'extraction.json':json.dumps(result,ensure_ascii=False,indent=2).encode(),'extraction.xlsx':xlsx}[name]
        mime={'extraction.txt':'text/plain; charset=utf-8','extraction.json':'application/json','extraction.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}[name]
        return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="{name}"'})
    return api


app=create_app()
