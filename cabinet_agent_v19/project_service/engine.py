from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from copy import deepcopy
import json
from pathlib import Path
import threading

from .core import Problem, Store, canonical, number, now
from .pdf_vision import prepare_pdf, VisionReader
from .engineering import ProductClient, merge_pages, assess, apply_answers, build_bom, catalog_hash
from .forms import issue_form, bom_form, read_form, read_issues, read_bom
from .deliverables import write_quote, write_procurement, write_production, write_dxf, CadClient

BUSY={'READING','CHECKING','CONFIRMING','GENERATING'}


class Engine:
    def __init__(self,settings,*,vision=None,products=None,cad=None):
        self.settings=settings;self.store=Store(settings)
        self.vision=vision or VisionReader(settings);self.products=products or ProductClient(settings)
        self.cad=cad or CadClient(settings)
        self.pool=ThreadPoolExecutor(max_workers=2)
        self.guard=threading.RLock();self.jobs={}
        # A process restart cannot leave a project eternally "processing".
        with self.store.connection() as db:
            rows=db.execute('SELECT payload FROM projects').fetchall()
        for row in rows:
            p=json.loads(row['payload'])
            if p['state'] in BUSY:
                p.update(state='FAILED',error={'code':'PROCESS_RESTARTED','message':'处理进程已重启，请重新识别；原文件与确认记录仍保存。'})
                self.store.save(p,'recover','system')

    def close(self):self.pool.shutdown(wait=True)

    def authorized_project(self,project_id,actor):
        p=self.store.get(project_id)
        if p['actor']!=actor:
            raise Problem('PROJECT_ACCESS_DENIED','该项目不属于当前Dify用户。',403)
        return p

    def add_file(self,project,kind,path):
        return self.store.artifact(project,kind,path)

    def form(self,p,kind):
        name=('问题确认单' if kind=='issues' else 'BOM确认单')+f"_V{p['revision']}.xlsx"
        path=self.store.folder(p['id'])/name
        (issue_form if kind=='issues' else bom_form)(self.store,p,path)
        self.add_file(p,kind,path)

    def evaluate(self,p):
        p['state']='CHECKING';self.store.save(p,'checking',p['actor'])
        p['issues']=assess(p['facts'],p['prepared'],self.products)
        if p['issues']:
            p['state']='NEEDS_CLARIFICATION';p['bom']=[];self.form(p,'issues')
        else:
            p['bom']=build_bom(p['facts'])
            for row in p['bom']:
                if row['material_code'] and row['supply_scope']=='OUR_PURCHASE':
                    # Any API price is reference-only until the engineer signs it.
                    try:row['price']['references']=self.products.prices(row['material_code'])[:5]
                    except Problem:row['price']['references']=[]
            p['state']='BOM_REVIEW';self.form(p,'bom')
        self.store.save(p,'review_ready',p['actor'])

    def recognize(self,p):
        p.update(bom=[],issues=[],approvals=[],delivery={})
        p.pop('quote_settings',None);p.pop('engineering',None)
        folder=self.store.folder(p['id']);prepared=[]
        for doc in p['documents']:
            prepared_doc=prepare_pdf(doc['path'],doc['document_id'],folder/'renders',self.settings.max_pages)
            for page in prepared_doc['pages']:page['document_role']=doc['role']
            prepared.append(prepared_doc)
        if sum(x['page_count'] for x in prepared)>self.settings.max_pages:
            raise Problem('TOTAL_PAGE_LIMIT','一个项目的PDF总页数超出配置上限。',422)
        p['prepared']=prepared
        extracted=self.vision.read(prepared)
        p['facts']=merge_pages(extracted,prepared)
        if p.get('user_notes'):
            p['facts']['notes'].append({'text':'上传者补充：'+p['user_notes'],'requires_confirmation':True,'evidence':[]})
        # Keep auditable extraction, not just a language-model paragraph.
        (folder/f"recognition_V{p['revision']}.json").write_text(canonical({'pages':extracted,'facts':p['facts']}),encoding='utf-8')
        self.evaluate(p)

    def submit_issues(self,p,path):
        if p['state_before_job']!='NEEDS_CLARIFICATION':
            raise Problem('ISSUE_PHASE_REQUIRED','当前阶段不接受问题确认单。')
        wb=read_form(path,self.store,p,'issues');approver,answers=read_issues(wb)
        apply_answers(p['facts'],p['issues'],answers,approver)
        p['approvals'].append({'kind':'issues','revision':p['revision'],'approver':approver,'at':now(),'answers':answers})
        p['revision']+=1
        self.evaluate(p)

    def check_catalog(self,p):
        for row in p['bom']:
            if row['supply_scope']!='OUR_PURCHASE' or row['procurement_mode']=='NON_CATALOG':continue
            current=self.products.exact(row['material_code'])
            if catalog_hash(current)!=row['catalog_hash']:
                raise Problem('CATALOG_CHANGED','产品数据已变化，需重新生成BOM并确认后交付。')

    def submit_bom(self,p,path):
        if p['state_before_job'] not in ('BOM_REVIEW','BOM_CONFIRMED','DELIVERY_PARTIAL','DELIVERED'):
            raise Problem('BOM_PHASE_REQUIRED','问题尚未解决，不能提交或跳过BOM确认。')
        wb=read_form(path,self.store,p,'bom')
        approver,edits,prices,settings,engineering,cabinet_edits=read_bom(wb,p)
        modified=False
        for item in p['facts']['items']:
            edit=edits[item['row_id']]
            for field,value in edit.get('fields',{}).items():
                if item.get(field)!=value:
                    item[field]=value;modified=True
                    item.setdefault('engineer_corrections',[]).append({'field':field,'value':value,'approver':approver,'at':now()})
                    if field=='shown_model':item['model_family']='';item['material_code']=''
            if edit['code'] and edit['code']!=str(item.get('material_code') or ''):
                item['material_code']=edit['code'];modified=True
            if edit['quantity'] not in (None,''):
                value=number(edit['quantity'],positive=True)
                if value!=number(item['quantity_per_cabinet']):
                    item['quantity_per_cabinet']=float(value);modified=True
        for cab in p['facts']['cabinets']:
            for field,value in cabinet_edits[cab['cabinet_id']].items():
                numeric=number(value,positive=True)
                if field=='quantity' and numeric!=numeric.to_integral_value():raise Problem('CABINET_QUANTITY_INVALID','柜体数量必须为正整数。',422)
                if cab.get(field) is None or numeric!=number(cab[field]):
                    cab[field]=float(numeric);modified=True
                    cab.setdefault('engineer_corrections',[]).append({'field':field,'value':float(numeric),'approver':approver,'at':now()})
        if modified:
            p['approvals'].append({'kind':'bom_changes','revision':p['revision'],'approver':approver,'edits':edits,'cabinet_edits':cabinet_edits,'at':now()})
            p['revision']+=1;p['message']='已按回传内容重新校验编码/数量，请确认新版本BOM。'
            self.evaluate(p);return
        try:self.check_catalog(p)
        except Problem as exc:
            if exc.code not in ('CATALOG_CHANGED','MATERIAL_CODE_UNAVAILABLE'):raise
            p['revision']+=1;p['message']=exc.message;self.evaluate(p);return
        for row in p['bom']:
            row['approval']=approver;row['engineer_note']=edits[row['row_id']]['note']
            if row['row_id'] in prices:row['price']=prices[row['row_id']]
        p['quote_settings']=settings;p['engineering']=engineering
        p['approvals'].append({'kind':'bom','revision':p['revision'],'approver':approver,'at':now(),
            'bom_hash':self.store.signature(p['bom'])})
        p['state']='BOM_CONFIRMED';self.store.save(p,'bom_confirmed',p['actor'])
        # Persist a new-version confirmation form for subsequent price/CAD supplementation.
        p['revision']+=1;self.form(p,'bom')
        self.generate(p)

    def generate(self,p):
        if not p['bom'] or not all(row.get('approval') for row in p['bom']):
            raise Problem('BOM_APPROVAL_REQUIRED','请先完成全部问题答复并回传已确认BOM。')
        self.check_catalog(p)
        p['state']='GENERATING';self.store.save(p,'generating',p['actor'])
        folder=self.store.folder(p['id'])/f"delivery_V{p['revision']}"
        folder.mkdir(exist_ok=True)
        self.add_file(p,'procurement',write_procurement(p,folder/'元器件采购清单.xlsx'))
        self.add_file(p,'production',write_production(p,folder/'排产技术资料.docx'))
        blockers=[]
        try:self.add_file(p,'quote',write_quote(p,folder/'报价文件.xlsx'))
        except Problem as exc:blockers.append({'output':'报价','code':exc.code,'message':exc.message})
        try:
            path=write_dxf(p,folder/'元件布置及接线关系.dxf')
            dwg=self.cad.convert(path,folder/'元件布置及接线关系.dwg')
            self.add_file(p,'dwg',dwg)
            # Intermediate DXF stays on disk for troubleshooting, not advertised as a DWG.
        except Problem as exc:blockers.append({'output':'DWG','code':exc.code,'message':exc.message})
        p['delivery']={'blockers':blockers,'at':now(),'cad_scope':'元件布置及接线关系图，需企业工程校核'}
        p['state']='DELIVERY_PARTIAL' if blockers else 'DELIVERED'
        self.store.save(p,'delivery_ready',p['actor'])

    def _job(self,project_id,action,path):
        p=self.store.get(project_id)
        original=deepcopy(p)
        try:
            if action=='recognize':self.recognize(p)
            elif action=='issues':self.submit_issues(p,path)
            elif action=='bom':self.submit_bom(p,path)
            elif action=='generate':self.generate(p)
        except Problem as exc:
            # Rejected submissions preserve the last usable phase and form.
            if action in ('issues','bom','generate'):
                p=original;p['state']=p.pop('state_before_job','FAILED')
            else:p['state']='FAILED'
            p['error']={'code':exc.code,'message':exc.message}
            self.store.save(p,'rejected' if action in ('issues','bom') else 'failed',p['actor'])
        except Exception:
            p['state']='FAILED';p['error']={'code':'INTERNAL_PROCESSING_ERROR','message':'处理失败，请管理员查看运行日志。源文件和历史确认仍保留。'}
            import logging;logging.getLogger(__name__).exception('project processing failed: %s',project_id)
            self.store.save(p,'failed',p['actor'])
        return p

    def run(self,p,action,path=None):
        with self.guard:
            current=self.store.get(p['id'])
            if current['state'] in BUSY:
                return self.describe(current)
            current.pop('error',None);current.pop('message',None)
            current['state_before_job']=current['state']
            if action=='recognize' and current.get('prepared'):
                current['revision']+=1
            current['state']={'recognize':'READING','issues':'CONFIRMING','bom':'CONFIRMING','generate':'GENERATING'}[action]
            self.store.save(current,'start_'+action,current['actor'])
            future=self.pool.submit(self._job,current['id'],action,path);self.jobs[current['id']]=future
        try:future.result(timeout=self.settings.wait_seconds)
        except FutureTimeout:pass
        return self.describe(self.store.get(p['id']))

    def describe(self,p):
        names={'UPLOADED':'资料已保存','READING':'正在识别PDF','CHECKING':'正在检查问题与产品','CONFIRMING':'正在校验确认表','GENERATING':'正在生成交付文件',
            'NEEDS_CLARIFICATION':'请回传问题确认单','BOM_REVIEW':'请回传已确认BOM','BOM_CONFIRMED':'BOM已确认','DELIVERY_PARTIAL':'部分文件已生成，仍有待补充项','DELIVERED':'文件已生成，CAD仍需工程校核','FAILED':'处理未完成'}
        lines=[f"**{p['name']}**",f"项目ID：`{p['id']}`",f"状态：{names.get(p['state'],p['state'])}；版本：{p['revision']}。"]
        if p['state'] in BUSY:lines.append('任务在后台继续运行，稍后选择“查看项目”。无需重复新建。')
        if p.get('message'):lines.append(p['message'])
        if p.get('error'):lines.append(p['error']['code']+'：'+p['error']['message'])
        artifacts=[]
        latest={}
        for a in p['artifacts']:latest[a['kind']]=a
        allowed={'issues'} if p['state']=='NEEDS_CLARIFICATION' else {'bom'} if p['state']=='BOM_REVIEW' else {'bom','procurement','production','quote','dwg'}
        for kind,a in latest.items():
            if kind not in allowed:continue
            # Do not display stale output after a new revision has started.
            if kind not in ('bom',) and a['revision']!=p['revision']:continue
            item=dict(a,url=self.store.link(a));artifacts.append(item)
            lines.append(f"[{a['filename']}]({item['url']})")
        for b in p.get('delivery',{}).get('blockers',[]):lines.append(b['output']+'待补充：'+b['message'])
        if p['state']=='NEEDS_CLARIFICATION':lines.append(f"还有 {len(p['issues'])} 项问题，填写黄色答复格和确认人后，选择“提交问题确认”。")
        if p['state'] in ('BOM_REVIEW','DELIVERY_PARTIAL'):
            lines.append('BOM确认表内可同时填写价格与CAD工程参数；缺少价格或CAD参数会分别阻止对应输出。')
        return {'project_id':p['id'],'revision':p['revision'],'state':p['state'],'message':'\n\n'.join(lines),'artifacts':artifacts,
                'error':p.get('error'),'blockers':p.get('delivery',{}).get('blockers',[])}
