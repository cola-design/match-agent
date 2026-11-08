"""Project stages and signed review forms; embedded into Dify 1.8 code nodes."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import hmac
import io
import json
import re
from urllib.parse import urlsplit
import uuid
import zipfile
from xml.etree import ElementTree as ET
from native.core import pack, unpack, new_state, stamp, number, j, YES, product_hash
from native.excel import workbook, sheet, signed_sheets, parse_form, norm
from native.legacy_selection import main as legacy_main

STAGE_NAMES={'READING':'读取项目资料','SOURCE_REVIEW':'待图纸和问题清单核对','MATCH_PENDING':'待数据库选型',
 'SELECTION_REVIEW':'待选型清单确认','PRICE_PENDING':'待查询报价参考价','QUOTE_REVIEW':'待采购报价表确认回传',
 'QUOTE_CONFIRMED':'报价已确认，可生成排产资料','PRODUCTION_READY':'排产资料已生成，待企业工艺复核'}
ROLE_NAMES={'MCB':'小型断路器','MCCB':'塑壳断路器','RCBO':'漏电断路器','RCCB':'剩余电流动作保护器',
 'residual_current_device_unspecified':'剩余电流保护器件','ATS':'自动转换开关','SPD':'电涌保护器','controller':'控制器',
 'contactor':'接触器','relay':'继电器','timer':'时间控制器','transformer':'变压器','dc_power_supply':'直流电源','fuse':'熔断器',
 'cable':'线缆','unknown':'待确认器件'}


def full_status(state):
    if not state:return '先填写项目名称并上传完整图纸PDF，发送“开始选型”。技术协议可选。'
    return '项目：%s；版本：%s；阶段：%s。\n流程：识图核对 → 数据库选型 → 选型确认 → 采购报价表 → 报价确认回传 → 排产技术资料。'%(
       state['name'],state['revision'],STAGE_NAMES.get(state['stage'],state['stage']))


def route_action(query,state_json,chat_files,pdf_files,technical_files,project_name,notes,action):
    try:state=unpack(state_json)
    except ValueError:return {'route':'STATUS','state_json':state_json,'message':'项目上下文不可读取，请在新对话开始。','upload_source':'FORM','has_technical':'NO'}
    text=str(query or '').strip() or str(action or '').strip()
    aliases={'新建项目':'开始选型','开始识图':'开始选型','开始PDF识图':'开始选型','提交问题确认':'提交问题清单',
             '提交BOM确认':'提交选型确认','提交bom表':'提交选型确认','提交报价确认':'提交报价表','生成排产':'生成排产资料',
             '生成交付文件':'生成排产资料','继续':'继续处理','重新核对':'修订资料','修改选型':'修订资料'}
    text=aliases.get(text,text);route='STATUS';message='';source='CHAT' if chat_files else 'FORM'
    if text in ('提交','继续处理','继续选型') and state:
        if chat_files and text=='提交':
            text={'SOURCE_REVIEW':'提交问题清单','SELECTION_REVIEW':'提交选型确认','QUOTE_REVIEW':'提交报价表'}.get(state['stage'],text)
        else:route={'MATCH_PENDING':'MATCH','PRICE_PENDING':'PRICE','SOURCE_REVIEW':'FORM','SELECTION_REVIEW':'FORM',
                    'QUOTE_REVIEW':'FORM','QUOTE_CONFIRMED':'PRODUCTION','PRODUCTION_READY':'PRODUCTION','READING':'NEW'}.get(state['stage'],'STATUS')
    if text=='开始选型':
        if state:message='当前对话已有项目。请先查看项目；新项目请使用新对话，避免覆盖。'
        else:
            files=chat_files or pdf_files
            if not isinstance(files,list) or len(files)!=1 or not str(files[0].get('filename') or files[0].get('name') or '').lower().endswith('.pdf'):
                message='请上传一份完整图纸PDF，再发送“开始选型”。'
            else:
                state=new_state(project_name,files,technical_files or []);state['schema']='cabinet-native-v28'
                state['notes']=str(notes or '')[:3000];state['technical_text']='';route='NEW'
    expected={'提交问题清单':('SOURCE_REVIEW','SOURCE_RETURN'),'提交选型确认':('SELECTION_REVIEW','SELECT_RETURN'),
              '提交报价表':('QUOTE_REVIEW','QUOTE_RETURN')}
    if text in expected:
        stage,target=expected[text]
        if not state or state['stage']!=stage:message='当前阶段不接受此类表格，请先“查看项目”。'
        elif not isinstance(chat_files,list) or len(chat_files)!=1 or not str(chat_files[0].get('filename') or chat_files[0].get('name') or '').lower().endswith('.xlsx'):
            message='请在聊天框上传当前版本的一份XLSX确认表，再提交。'
        else:route=target
    if text=='生成排产资料':
        if state.get('stage') in ('QUOTE_CONFIRMED','PRODUCTION_READY'):route='PRODUCTION'
        else:message='采购报价表尚未确认回传，不能生成排产技术资料。'
    if text=='保存项目资料' and state:route='ARCHIVE'
    if text=='修订资料':
        if state.get('stage') and state['stage']!='READING':
            state['stage']='SOURCE_REVIEW';state['revision']+=1;state['bom']=[]
            for key in ('confirmed_quote_hash','quote_approver','selection_approver','engineering','form'):state.pop(key,None)
            for item in state['facts']['items']:item.pop('selected',None)
            stamp(state,'source_revision_requested');route='FORM'
        else:message='请先完成图纸读取，再修订项目资料。'
    return {'route':route,'state_json':pack(state) if state else '',
            'message':message or full_status(state),'upload_source':source,'has_technical':'YES' if technical_files else 'NO'}


def save_technical(state_json,texts):
    state=unpack(state_json);texts=texts if isinstance(texts,list) else []
    text='\n'.join(x for x in texts if isinstance(x,str))
    if len(text)>50000:return {'valid':'NO','state_json':state_json,'message':'技术协议文字超过当前项目预算，未截断后继续。'}
    state['technical_text']=text
    state['technical_read_status']='text_available_for_review' if text.strip() else 'manual_review_required'
    return {'valid':'YES','state_json':pack(state),'message':''}


def bridge_recognition(state_json,result_json,result_pages):
    state=unpack(state_json);result=json.loads(result_json)
    if result.get('details_in')=='result_pages':
        joined={}
        for packet in result_pages:
            data=json.loads(packet)
            for key in ('items','drawings','issues','document_annotations','circuits'):
                joined.setdefault(key,[]).extend(data.get(key,[]))
        joined['summary']=result['summary'];result=joined
    if len(result.get('items',[]))>100:raise ValueError('识别候选超过100项，请按柜组分项目；未删除超出的条目。')
    cabs=[];drawing_to_cab={};tags_to_cab={}
    for d in result.get('drawings',[]):
        tags=tuple(sorted(set(d.get('cabinet_tags') or [])))
        group=tags_to_cab.get(tags) if tags else None
        if not group:
            group='G%d'%(len(cabs)+1)
            cabs.append({'cabinet_id':group,'source_tags':list(tags),'quantity':None,'width_mm':None,'height_mm':None,'depth_mm':None,
                         'notes':[],'evidence':[{'document_id':'D1','page':d['page'],'bbox':[v/1000 for v in d['bbox']] if d.get('bbox') else None,'quote':d.get('title') or ''}]})
            if tags:tags_to_cab[tags]=group
        drawing_to_cab[d['id']]=group
    items=[]
    for index,x in enumerate(result.get('items',[]),1):
        p=dict(x.get('literal_parameters') or {});p.update(x.get('associated_parameters') or {})
        req={}
        for a,b in [('rated_current_a','rated_current_a'),('pole_marking','pole_count'),('trip_curve','trip_curve'),
                    ('residual_current_ma','leakage_current_ma'),('voltage_v','voltage_v'),('current_a','current_a'),
                    ('voltage_pair_v','voltage_pair_v'),('apparent_power_va','apparent_power_va'),('ats_class','ats_class')]:
            if a in p:req[b]=p[a]
        evidence=[{'document_id':'D1','page':x['page'],'bbox':[v/1000 for v in x['bbox']] if x.get('bbox') else None,'quote':x['raw']}]
        pending=[q['message'] for q in result.get('issues',[]) if q.get('item_id')==x['id']]
        items.append({'row_id':'I%04d'%index,'cabinet_id':drawing_to_cab.get(x.get('drawing_id'),''),'device_tag':x.get('circuit') or '',
                      'role':ROLE_NAMES.get(x.get('category'),'待确认器件'),'shown_model':x['raw'],'model_family':'','requirements':req,
                      'quantity_per_cabinet':None,'unit':'m' if x.get('category')=='cable' else '台','supply_scope':'OUR_PURCHASE',
                      'procurement_mode':'CATALOG','manufacturer_requested':'','material_code':'','evidence':evidence,
                      'recognition_pending':pending,'recognition_meaning':x.get('meaning_cn','')})
    if not items:raise ValueError('本次没有可核对的器件候选，不能生成空清单继续选型。请查看识图节点的失败或未覆盖区域。')
    if not cabs:cabs=[{'cabinet_id':'G1','source_tags':[],'quantity':None,'width_mm':None,'height_mm':None,'depth_mm':None,'notes':[],'evidence':[]}]
    notes=[{'text':x['raw'],'kind':x['kind'],'evidence':[{'document_id':'D1','page':x['page'],'bbox':x.get('bbox')}]} for x in result.get('document_annotations',[])]
    state['facts']={'cabinets':cabs,'items':items,'notes':notes,'rules':[],'connections':[],'recognition_issues':[]}
    state['recognition_summary']=result.get('summary',{});state['recognition_issues']=result.get('issues',[])
    state['recognized_circuits']=result.get('circuits',[])
    state['stage']='SOURCE_REVIEW';state['revision']+=1;stamp(state,'spatial_recognition_imported')
    return {'state_json':pack(state),'message':full_status(state)}


def complete_book(book):
    cells=json.loads(book['payload_json'])['cells'];raw=base64.b64decode(book['template_base64']);out=io.BytesIO()
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dst:
        names=[s.attrib['name'] for s in ET.fromstring(src.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns)]
        priority=['[Content_Types].xml','_rels/.rels','xl/workbook.xml','xl/_rels/workbook.xml.rels']
        for path in priority+[p for p in src.namelist() if p not in priority]:
            content=src.read(path)
            match=re.fullmatch(r'xl/worksheets/sheet(\d+)\.xml',path)
            if match:
                name=names[int(match[1])-1];tree=ET.fromstring(content)
                for cell in tree.findall('.//s:c',ns):
                    text=cell.find('s:is/s:t',ns)
                    if text is not None:text.text=str(cells[name+'!'+cell.attrib['r']])
                content=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
            dst.writestr(path,content)
    encoded=base64.b64encode(out.getvalue()).decode()
    if len(encoded)>60000:raise ValueError('完整文件超过当前传输预算，未截断文件。')
    return {'file_base64':encoded,'filename':book['filename']+'.xlsx'}


def append_approver(state,sheets,instruction):
    sheets.append(sheet('填写说明',['记录类型','行ID','项目名称','项目ID','版本','确认人','填写说明'],
        [['SETTINGS','APPROVER',state['name'],state['project_id'],state['revision'],'',instruction]],{5},[14,24,30,40,12,20,100]))
    return sheets


def make_stage_form(state_json,markup,discount):
    state=unpack(state_json);stage=state['stage'];sheets=[]
    if stage=='SOURCE_REVIEW':
        headers=['记录类型','行ID','原图型号','原图解释','待核对内容','确认柜组','确认回路','确认元件类别','确认型号','确认技术参数JSON','确认单柜数量','计量单位','供货范围','采购方式','物料编码','非标依据','指定厂商','同意替代','确认','处理方式','剔除或补录依据']
        rows=[['SOURCE',x['row_id'],x['shown_model'],x.get('recognition_meaning',''),'；'.join(x['recognition_pending']),x['cabinet_id'],x['device_tag'],x['role'],x['shown_model'],j(x['requirements']),x.get('quantity_per_cabinet') if state.get('source_reviewed') else None,x['unit'],x['supply_scope'],x['procurement_mode'],x['material_code'],x.get('non_catalog_reason',''),x.get('manufacturer_requested') or '待核对','','','KEEP',''] for x in state['facts']['items']]
        widths=[14,16,42,65,75,16,16,24,42,70,18,14,24,24,24,55,24,18,12,18,64]
        sheets.append(sheet('识图与问题清单',headers,rows,set(range(5,len(headers))),widths))
        freeids=['ADD%d'%i for i in range(1,106) if 'ADD%d'%i not in {x['row_id'] for x in state['facts']['items']}][:5]
        additions=[['SOURCEADD',rid,'','','人工补录；不用的整行留空','','','','','',None,'','OUR_PURCHASE','CATALOG','','','待核对','','','ADD',''] for rid in freeids]
        sheets.append(sheet('手工补录',headers,additions,set(range(5,len(headers))),widths))
        cabs=[['CAB',c['cabinet_id'],'、'.join(c['source_tags']),'、'.join(c['source_tags']),c.get('quantity') if state.get('source_reviewed') else None,c.get('width_mm'),c.get('height_mm'),c.get('depth_mm'),''] for c in state['facts']['cabinets']]
        cabs += [['CAB','NEWG%d'%i,'新增柜组（不用请留空）','',None,None,None,None,''] for i in range(1,4) if not any(c['cabinet_id']=='NEWG%d'%i for c in state['facts']['cabinets'])]
        sheets.append(sheet('柜组数量',['记录类型','行ID','原图柜号','确认柜号','确认柜体数量','宽mm','高mm','深mm','确认'],cabs,{3,4,5,6,7,8}))
        notes=[['TECH','NOTE%d'%i,n['text'],''] for i,n in enumerate(state['facts']['notes'],1)]
        sheets.append(sheet('技术原文',['记录类型','行ID','原文','补充说明'],notes,{3},[14,20,100,80]))
        agreement=[['TECH','AGREEMENT_%d'%(i//28000+1),state['technical_text'][i:i+28000]] for i in range(0,len(state.get('technical_text','')),28000)]
        if agreement:sheets.append(sheet('技术协议原文',['记录类型','行ID','协议内容'],agreement,widths=[14,28,120]))
        coverage=[['TECH','COVERAGE',j(state.get('recognition_summary',{}))]]
        if any(d.get('role')=='technical_agreement' for d in state.get('documents',[])):
            coverage.append(['TECH','AGREEMENT_STATUS','技术协议已提取文字，仍需对照原件核对适用范围' if state.get('technical_read_status')=='text_available_for_review' else '技术协议未提取到可读文字，请人工对照原附件核对，不代表协议没有技术要求'])
        coverage += [['TECH','CHECK_%d'%i,j(issue)] for i,issue in enumerate(state.get('recognition_issues',[]),1)]
        sheets.append(sheet('识图覆盖与待核对',['记录类型','行ID','识图覆盖或未定位内容'],coverage,widths=[14,28,140]))
        circuits=[['TECH','CIRCUIT_%d'%i,j(c)] for i,c in enumerate(state.get('recognized_circuits',[]),1)]
        if circuits:sheets.append(sheet('回路识别候选',['记录类型','行ID','原图回路候选（仍需核对）'],circuits,widths=[14,28,140]))
        text='请对照全部原PDF核对器件、漏项、回路和数量；明确填黄色格，确认填“是”。误识别行填EXCLUDE并写剔除依据，不直接删行；漏项在手工补录填写，最多5行，新增柜组可用NEWG1至NEWG3，不用的留空。柜号数量不自动等于柜体数量。协议原文件随本会话保留；影响选型的技术要求必须写入对应元件的确认技术参数JSON，补充说明只作资料保留，不自动改变选型。表格填写实际值，不使用公式。'
        sheets.append(sheet('完整性确认',['记录类型','行ID','核对结论','确认'],[['SETTINGS','SOURCE_COMPLETE','','']],{2,3},[14,28,100,14]))
        kind='SOURCE';title='识图与问题确认清单'
    elif stage=='SELECTION_REVIEW':
        rows=[['BOM',x['row_id'],x['cabinet_id'],x['device_tag'],x['role'],x['selected']['material_code'] if x.get('selected') else '',
              x['selected'].get('description') if x.get('selected') else x['shown_model'],x['quantity_per_cabinet'],x['unit'],x['supply_scope'],'',''] for x in state['facts']['items']]
        sheets.append(sheet('选型确认',['记录类型','行ID','柜组','回路','元件类别','物料编码','匹配产品','单柜数量','单位','供货范围','修改物料编码','确认'],rows,{10,11}))
        kind='SELECTION';title='产品选型确认表';text='逐项核对产品和参数。修改编码会重新查库匹配，此阶段不读取价格。'
    elif stage=='QUOTE_REVIEW':
        rows=[]
        for x in state['bom']:
            if x['supply_scope']!='OUR_PURCHASE':continue
            references=x.get('price_references',[]);one=references[0] if len(references)==1 else {}
            ref=one.get('unit_price_ex_tax') if one.get('pricing_unit')==x['unit'] else None
            suggested=None
            if ref is not None:suggested=str((number(ref)*(1+number(markup))*(1-number(discount))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
            rows.append(['QUOTE',x['row_id'],x['cabinet_id'],x['device_tag'],x['description'],x['material_code'],x['quantity'],x['unit'],j(references),ref,one.get('tax_rate'),suggested,'','',''])
        sheets.append(sheet('采购报价确认',['记录类型','行ID','柜组','回路','产品规格','物料编码','总数量','单位','API参考记录(未审批)','确认未税采购单价','确认税率','确认未税销售单价','价格依据','供货备注','确认'],rows,{9,10,11,12,13,14},[14,16,16,16,48,24,16,12,75,24,18,24,60,60,12]))
        sheets.append(sheet('不计价范围',['记录类型','行ID','产品规格','数量','单位','供货范围'],[['BOM',x['row_id'],x['description'],x['quantity'],x['unit'],x['supply_scope']] for x in state['bom'] if x['supply_scope']!='OUR_PURCHASE']))
        sheets.append(sheet('报价整体确认',['记录类型','行ID','确认说明','确认'],[['SETTINGS','QUOTE_APPROVAL','','']],{2,3},[14,28,100,14]))
        kind='QUOTE';title='采购报价确认表_待确认';text='参考价不是已批准价格。逐项确认数量、采购价、销售价、税率和真实价格依据，在整体确认填写结论。报价确认前不生成排产资料。'
    else:raise ValueError('当前阶段没有可生成的确认表')
    append_approver(state,sheets,text);signed_sheets(state,kind,sheets)
    result=complete_book(workbook(sheets,title+'_R'+str(state['revision'])))
    return dict(result,state_json=pack(state),message=full_status(state)+'\n'+text)


def approval_text(value):
    text=str(value or '').strip()
    return len(text)>=4 and not re.search(r'尚未|未完成|未核对|未确认|待核对|待确认|不通过|不同意|不确认|未通过',text)


def reviewed_source_item(item,row,cabinets):
    if row['确认'] not in YES:raise ValueError('所有保留或补录条目都需核对并明确确认')
    if row['确认柜组'] not in cabinets:raise ValueError('元件所属柜组不在已确认柜组中')
    req=json.loads(row['确认技术参数JSON'] or '{}')
    if not isinstance(req,dict):raise ValueError('技术参数JSON必须为对象')
    q=number(row['确认单柜数量'],True);scope=row['供货范围'];mode=row['采购方式']
    if scope not in ('OUR_PURCHASE','CUSTOMER_SUPPLIED','UTILITY_SUPPLIED','RESERVED'):raise ValueError('供货范围代码不正确')
    if mode not in ('CATALOG','NON_CATALOG'):raise ValueError('采购方式必须为CATALOG或NON_CATALOG')
    if mode=='NON_CATALOG' and not row['非标依据'].strip():raise ValueError('非标采购需明确规格和依据')
    if not row['确认型号'].strip() or not row['确认元件类别'].strip() or not row['计量单位'].strip():raise ValueError('型号、类别和单位必须填写')
    if '待确认' in row['确认元件类别']:raise ValueError('元件类别尚未核实，不能进入自动选型')
    brand=row['指定厂商'].strip()
    if not brand or brand in ('待核对','待确认'):raise ValueError('请核对指定厂商，原图未指定时明确填“未指定”')
    item.update(cabinet_id=row['确认柜组'],device_tag=row['确认回路'],role=row['确认元件类别'],shown_model=row['确认型号'],
                requirements=req,quantity_per_cabinet=float(q),unit=row['计量单位'],supply_scope=scope,procurement_mode=mode,
                material_code=row['物料编码'],non_catalog_reason=row['非标依据'],
                manufacturer_requested='' if brand=='未指定' else brand,substitution_approved=row['同意替代'] in YES)
    return item


def xlsx_markdown(encoded):
    text=str(encoded or '').strip()
    if text.startswith('data:'):text=text.split(',',1)[1]
    raw=base64.b64decode(text,validate=True)
    if len(raw)>2000000:raise ValueError('回传确认表超过2MB，请删除无关图片和嵌入对象')
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    result=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos=z.infolist()
        if len(infos)>100 or sum(i.file_size for i in infos)>16000000:raise ValueError('回传确认表内容超过解析容量')
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(t.text or '' for t in si.iter('{'+ns['s']+'}t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets={r.attrib['Id']:r.attrib['Target'] for r in rels if r.attrib.get('TargetMode')!='External'}
        for sh in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns):
            target=targets[sh.attrib['{'+ns['r']+'}id']]
            if '..' in target or '\\' in target:raise ValueError('确认表工作表路径不正确')
            path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            tree=ET.fromstring(z.read(path));rows=[];maxcol=0
            for row in tree.findall('s:sheetData/s:row',ns):
                cells={}
                for cell in row.findall('s:c',ns):
                    if cell.find('s:f',ns) is not None:raise ValueError('确认表请填写实际值，不使用公式；已保留项目，未按缓存公式值审批')
                    ref=cell.attrib.get('r','');m=re.fullmatch(r'([A-Z]+)\d+',ref)
                    if not m:raise ValueError('确认表单元格坐标无效')
                    colno=0
                    for ch in m[1]:colno=colno*26+ord(ch)-64
                    if colno>64:raise ValueError('确认表列数超出范围')
                    v=cell.find('s:v',ns);value=v.text or '' if v is not None else ''
                    typ=cell.attrib.get('t')
                    if typ=='s':value=strings[int(value)]
                    elif typ=='inlineStr':value=''.join(t.text or '' for t in cell.findall('.//s:t',ns))
                    elif typ=='e':raise ValueError('确认表有Excel错误单元格，请修正后回传')
                    if '|' in value:raise ValueError('确认表不能含竖线分隔符，请使用中文斜线或其他描述')
                    value=' '.join(str(value).splitlines()).strip()
                    cells[colno]=value;maxcol=max(maxcol,colno)
                if any(cells.values()):rows.append(cells)
            if not rows:continue
            if len(rows)>120:raise ValueError('确认表行数超出范围')
            width=max(rows[0]);headers=[rows[0].get(i,'') for i in range(1,width+1)]
            if any(not h for h in headers) or len(set(headers))!=len(headers):raise ValueError('确认表列标题缺失或重复')
            result.append('| '+' | '.join(headers)+' |')
            result.append('| '+' | '.join('---' for _ in headers)+' |')
            for cells in rows[1:]:
                if any(i>width and v for i,v in cells.items()):raise ValueError('确认表有无标题的附加列')
                result.append('| '+' | '.join(cells.get(i,'') for i in range(1,width+1))+' |')
    value='\n'.join(result)
    if len(value)>600000:raise ValueError('确认表文字超出容量')
    return value


def read_stage_return(state_json,encoded,kind):
    state=unpack(state_json);stage={'SOURCE':'SOURCE_REVIEW','SELECTION':'SELECTION_REVIEW','QUOTE':'QUOTE_REVIEW'}
    if kind not in stage:raise ValueError('当前版本不接受此类确认表')
    if state['stage']!=stage[kind]:raise ValueError('回传阶段不匹配')
    records,approver=parse_form(state,xlsx_markdown(encoded),kind)
    if kind=='SOURCE':
        cabinets={c['cabinet_id']:c for c in state['facts']['cabinets']}
        for (typ,cid),row in records.items():
            if typ=='CAB' and cid not in cabinets and (row.get('确认柜号') or row.get('确认') in YES):
                cabinets[cid]={'cabinet_id':cid,'source_tags':[],'quantity':None,'width_mm':None,'height_mm':None,'depth_mm':None}
        for cid,c in cabinets.items():
            row=records[('CAB',cid)]
            if row['确认'] not in YES:raise ValueError('柜组数量需明确确认')
            if not row['确认柜号'].strip():raise ValueError('请填写实际柜号或经确认的柜组标识')
            c['source_tags']=[x for x in re.split(r'[,，、;/\s]+',row['确认柜号'].strip()) if x]
            q=number(row['确认柜体数量'],True)
            if q!=q.to_integral_value():raise ValueError('柜体数量必须是正整数')
            c['quantity']=int(q)
            for key,label in [('width_mm','宽mm'),('height_mm','高mm'),('depth_mm','深mm')]:
                if row.get(label):c[key]=float(number(row[label],True))
        kept=[]
        for item in state['facts']['items']:
            row=records[('SOURCE',item['row_id'])]
            if row['处理方式']=='EXCLUDE':
                if row['确认'] not in YES or not approval_text(row['剔除或补录依据']):raise ValueError('剔除误识别项需填写明确依据并确认')
                state.setdefault('excluded_items',[]).append({'item':item,'reason':row['剔除或补录依据'],'reviewer':approver})
                continue
            if row['处理方式']!='KEEP':raise ValueError('原识别行的处理方式只能为KEEP或EXCLUDE')
            kept.append(reviewed_source_item(item,row,cabinets))
        for (typ,rid),row in records.items():
            if typ!='SOURCEADD':continue
            if not any(row.get(k) for k in ('确认型号','确认柜组','确认单柜数量','剔除或补录依据')) and row['确认'] not in YES:continue
            if row['处理方式']!='ADD' or not approval_text(row['剔除或补录依据']):raise ValueError('人工补录需填写明确图纸位置或技术依据')
            item={'row_id':rid,'recognition_pending':[],'recognition_meaning':'人工补录','evidence':[{'source':'human','reviewer':approver,'quote':row['剔除或补录依据']}],'model_family':''}
            kept.append(reviewed_source_item(item,row,cabinets))
        if not kept:raise ValueError('没有保留任何元件，不能进入选型')
        if len(kept)>100:raise ValueError('元件超过100项，请按柜组分项目')
        state['facts']['items']=kept;state['facts']['cabinets']=list(cabinets.values())
        for (typ,rid),row in records.items():
            if typ=='TECH' and row.get('补充说明','').strip():
                state['facts']['notes'].append({'text':'工程师补充（'+rid+'）：'+row['补充说明'],'kind':'human_note','reviewer':approver})
        complete=records[('SETTINGS','SOURCE_COMPLETE')]
        if complete['确认'] not in YES or not approval_text(complete['核对结论']):raise ValueError('请填写完整性核对结论并明确确认')
        state['source_reviewed']=True;state['stage']='MATCH_PENDING'
    elif kind=='SELECTION':
        changed=False
        for item in state['facts']['items']:
            row=records[('BOM',item['row_id'])]
            if row['确认'] not in YES:raise ValueError('全部选型行需要明确确认')
            code=row.get('修改物料编码','').strip()
            if code and code!=(item.get('selected') or {}).get('material_code'):
                item['material_code']=code;item.pop('selected',None);changed=True
        state['stage']='MATCH_PENDING' if changed else 'PRICE_PENDING'
        if not changed:state['selection_approver']=approver
    elif kind=='QUOTE':
        for row in state['bom']:
            if row['supply_scope']!='OUR_PURCHASE':continue
            answer=records[('QUOTE',row['row_id'])]
            if answer['确认'] not in YES:raise ValueError('报价每行必须明确确认')
            cost=number(answer['确认未税采购单价']);sell=number(answer['确认未税销售单价']);tax=number(answer['确认税率'])
            basis=answer['价格依据'].strip()
            if cost<0 or sell<0 or not 0<=tax<=1:raise ValueError('金额不得为负，税率应为0至1')
            if not basis or re.search(r'估算|演示|模拟|占位|REFERENCE[_ -]?(?:ESTIMATE|ONLY)|DEMO',basis,re.I):raise ValueError('请填写经过确认的真实价格依据，不能将参考估算直接当正式报价')
            row['price']={'cost_ex_tax':str(cost),'sell_ex_tax':str(sell),'tax_rate':str(tax),'basis':basis,'confirmed_by':approver,'note':answer['供货备注']}
        overall=records[('SETTINGS','QUOTE_APPROVAL')]
        if overall['确认'] not in YES or not approval_text(overall['确认说明']):raise ValueError('请填写报价整体确认说明并明确确认')
        state['quote_approver']=approver;state['confirmed_quote_hash']=hashlib.sha256(j(state['bom']).encode()).hexdigest()
        state['stage']='QUOTE_CONFIRMED'
    state['revision']+=1;state.pop('form',None);stamp(state,'return_'+kind,approver)
    return {'state_json':pack(state),'message':full_status(state),'route':{'MATCH_PENDING':'MATCH','PRICE_PENDING':'PRICE',
            'QUOTE_CONFIRMED':'PRODUCTION','PRODUCTION_READY':'STATUS'}.get(state['stage'],'STATUS')}


def prepare_lookup(state_json,api_base,mode):
    state=unpack(state_json);base=str(api_base or '').strip().rstrip('/');parts=urlsplit(base)
    expected=('MATCH_PENDING',) if mode=='MATCH' else ('PRICE_PENDING',) if mode=='PRICE' else ('QUOTE_CONFIRMED','PRODUCTION_READY')
    if state['stage'] not in expected:return {'state_json':state_json,'queries':[],'valid':'NO','message':'当前阶段不能执行该查询'}
    if parts.scheme not in ('http','https') or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username or parts.password:
        return {'state_json':state_json,'queries':[],'valid':'NO','message':'请配置产品API根地址，仅协议、域名或IP与端口；不包含/v1、/live、查询串或账号。'}
    queries=[]
    if mode=='MATCH':
        for item in state['facts']['items']:
            if item['supply_scope']!='OUR_PURCHASE' or item['procurement_mode']=='NON_CATALOG':continue
            code=str(item.get('material_code') or '').strip()
            if code and not re.fullmatch(r'\d{8,16}',code):return {'state_json':state_json,'queries':[],'valid':'NO','message':'物料编码必须为8至16位数字'}
            queries.append(j({'row_id':item['row_id'],'item':item,'url':base+'/v1/products/'+code if code else base+'/v1/products/search',
                              'q':item['shown_model'],'model':item.get('model_family',''),'mode':mode}))
    elif mode=='PRICE':
        for row in state['bom']:
            if row['supply_scope']=='OUR_PURCHASE' and row.get('material_code'):
                queries.append(j({'row_id':row['row_id'],'url':base+'/v1/prices/'+row['material_code'],'q':'','model':'','mode':mode}))
    else:
        for row in state['bom']:
            if row['supply_scope']=='OUR_PURCHASE' and row.get('material_code'):
                queries.append(j({'row_id':row['row_id'],'url':base+'/v1/products/'+row['material_code'],
                                  'q':'','model':'','mode':mode,'catalog_hash':row['catalog_hash']}))
    return {'state_json':state_json,'queries':queries,'valid':'YES','message':''}


def lookup_card(card):
    value=json.loads(card)
    return {'url':value['url'],'q':value['q'],'model':value['model'],'card_json':card}


def lookup_result(card_json,body,status_code,error_message=None):
    card=json.loads(card_json);out={'row_id':card['row_id'],'mode':card['mode'],'ok':False,'error':'','selected':None,'alternatives':[],'prices':[]}
    try:
        status=int(status_code or 0)
        if status==0:
            match=re.search(r'status code\s+(\d{3})',str(error_message or ''),re.I)
            if match:status=int(match[1])
        if status!=200:raise ValueError('产品API鉴权失败' if status in (401,403) else '产品API HTTP '+str(status) if status else '产品API网络连接失败或超时，请核对服务地址及Dify容器网络')
        values=json.loads(body)
        if not isinstance(values,list):raise ValueError('API返回格式不符合约定，应为数组')
        if card['mode']=='PRICE':out['prices']=values
        elif card['mode']=='VERIFY':
            if len(values)!=1 or product_hash(values[0])!=card['catalog_hash']:
                out['catalog_changed']=True
                raise ValueError('产品资料版本发生变化，需重新选型并确认采购报价')
        else:
            item=card['item'];req={'request_id':item['row_id'],'component_role':item['role'],'shown_model':item['shown_model'],
                 'requirements':item['requirements'],'mapping_status':'USER_CONFIRMED','missing_parameters':[],'unit':item['unit']}
            brand=item.get('manufacturer_requested','').strip()
            if brand and not item.get('substitution_approved'):
                values=[p for p in values if brand.upper() in str(p.get('manufacturer') or '').upper()]
            chosen=legacy_main('',j(req),values);selection=json.loads(chosen['item_json'])
            if selection.get('match_status')=='MATCHED':
                code=selection.get('recommended',{}).get('material_code');matches=[x for x in values if str(x.get('material_code'))==str(code)]
                if len(matches)==1:out['selected']=dict(matches[0],catalog_hash=product_hash(matches[0]))
            out['alternatives']=selection.get('alternatives',[])[:6]
        out['ok']=True
    except (ValueError,TypeError,KeyError) as exc:out['error']=str(exc)
    return {'result_json':j(out)}


def finish_lookup(state_json,results,mode):
    state=unpack(state_json);rows=[json.loads(x) for x in results];by={x['row_id']:x for x in rows}
    expected={x['row_id'] for x in state['facts']['items'] if x['supply_scope']=='OUR_PURCHASE' and x['procurement_mode']!='NON_CATALOG'} if mode=='MATCH' else {x['row_id'] for x in state['bom'] if x['supply_scope']=='OUR_PURCHASE' and x.get('material_code')}
    if len(by)!=len(rows) or set(by)!=expected or any(x.get('mode')!=mode for x in rows):
        return {'state_json':state_json,'valid':'NO','message':'产品查询返回缺行、重复行或模式不一致；保留项目，未输出不完整清单'}
    failures=[x['error'] for x in rows if not x['ok']]
    if failures:
        if any(x.get('catalog_changed') for x in rows):
            state['stage']='MATCH_PENDING';state['bom']=[];state.pop('confirmed_quote_hash',None);state['revision']+=1
            return {'state_json':pack(state),'valid':'NO','message':'产品资料发生变化，已暂停排产。发送“继续处理”重新选型，随后重新确认报价。'}
        return {'state_json':state_json,'valid':'NO','message':'；'.join(dict.fromkeys(failures))+'；保留当前项目，修正配置后发送“继续处理”。'}
    if mode=='VERIFY':return {'state_json':state_json,'valid':'YES','message':'当前产品资料复核通过'}
    if mode=='MATCH':
        missing=[]
        for item in state['facts']['items']:
            if item['supply_scope']!='OUR_PURCHASE' or item['procurement_mode']=='NON_CATALOG':item['selected']=None;continue
            record=by.get(item['row_id'])
            if record is None:return {'state_json':state_json,'valid':'NO','message':'产品查询缺少返回行，未生成半份清单'}
            item['selected']=record['selected'];item['alternatives']=record['alternatives']
            if not record['selected']:missing.append(item['row_id'])
        if missing:
            state['stage']='SOURCE_REVIEW';state['revision']+=1
            for item in state['facts']['items']:
                if item['row_id'] in missing:item['recognition_pending']=['数据库未找到唯一且规格匹配的产品；请核对型号、参数或填写准确物料编码；确属非标时填写依据。']
        else:
            cabinets={x['cabinet_id']:x for x in state['facts']['cabinets']};bom=[]
            for item in state['facts']['items']:
                product=item.get('selected') or {};q=number(item['quantity_per_cabinet'],True)*number(cabinets[item['cabinet_id']]['quantity'],True)
                bom.append({'row_id':item['row_id'],'cabinet_id':item['cabinet_id'],'device_tag':item['device_tag'],'role':item['role'],
                            'description':product.get('description') or item['shown_model'],'material_code':str(product.get('material_code') or ''),
                            'manufacturer':product.get('manufacturer') or item.get('manufacturer_requested') or '',
                            'quantity_per_cabinet':item['quantity_per_cabinet'],'cabinet_quantity':cabinets[item['cabinet_id']]['quantity'],
                            'quantity':float(q),'unit':item['unit'],'supply_scope':item['supply_scope'],'catalog_hash':product.get('catalog_hash',''),
                            'price_references':[],'price':{},'approval':''})
            state['bom']=bom;state['stage']='SELECTION_REVIEW';state['revision']+=1
    else:
        for row in state['bom']:
            record=by.get(row['row_id']);row['price_references']=record['prices'] if record else []
        state['stage']='QUOTE_REVIEW';state['revision']+=1
    stamp(state,'lookup_'+mode)
    return {'state_json':pack(state),'valid':'YES','message':full_status(state)}


def production_files(state_json):
    state=unpack(state_json)
    if state['stage'] not in ('QUOTE_CONFIRMED','PRODUCTION_READY'):raise ValueError('报价未确认，禁止生成排产技术资料')
    if hashlib.sha256(j(state['bom']).encode()).hexdigest()!=state.get('confirmed_quote_hash'):raise ValueError('报价确认后内容发生变化，请重新确认报价')
    purchase=[];notes=[];net_total=Decimal(0);tax_total=Decimal(0)
    for r in state['bom']:
        if r['supply_scope']!='OUR_PURCHASE':continue
        p=r['price'];net=(number(p['sell_ex_tax'])*number(r['quantity'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        tax=(net*number(p['tax_rate'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP);net_total+=net;tax_total+=tax
        purchase.append(['QUOTE',r['row_id'],r['cabinet_id'],r['device_tag'],r['description'],r['material_code'],r['quantity'],r['unit'],p['cost_ex_tax'],p['sell_ex_tax'],p['tax_rate'],str(net),str(tax),str(net+tax),p['basis']])
    quote=complete_book(workbook([sheet('采购报价表',['记录类型','行ID','柜组','回路','产品规格','物料编码','数量','单位','确认未税采购单价','确认未税销售单价','税率','未税金额','税额','含税金额','依据'],purchase),
      sheet('报价汇总',['项目名称','未税合计','税额','含税合计','确认人'],[[state['name'],str(net_total),str(tax_total),str(net_total+tax_total),state['quote_approver']]])], '已确认采购报价表_R'+str(state['revision'])))
    cabs=[['CAB',c['cabinet_id'],'、'.join(c.get('source_tags',[])),c['quantity'],c.get('width_mm'),c.get('height_mm'),c.get('depth_mm')] for c in state['facts']['cabinets']]
    material=[['BOM',r['row_id'],r['cabinet_id'],r['device_tag'],r['role'],r['description'],r['material_code'],r['quantity'],r['unit'],r['supply_scope']] for r in state['bom']]
    note_rows=[['NOTE',str(i),n['text']] for i,n in enumerate(state['facts']['notes'],1)]
    for index in range(0,len(state.get('technical_text','')),28000):
        note_rows.append(['NOTE','AGREEMENT_%d'%(index//28000+1),state['technical_text'][index:index+28000]])
    production=complete_book(workbook([sheet('柜体参数',['记录类型','行ID','原柜号','数量','宽mm','高mm','深mm'],cabs),
      sheet('已确认物料',['记录类型','行ID','柜组','回路','元件名称','规格','物料编码','数量','单位','供货范围'],material),
      sheet('技术资料原文',['记录类型','行ID','资料内容'],note_rows,widths=[14,20,120]),
      sheet('工艺复核',['项目','内容','责任人记录'],[['结构工艺','按实际柜体尺寸、安装尺寸与企业工艺校核',''],['接线设计','确认端子、接线、线径、母排与接地；未自动代替设计',''],['检验','按企业适用规范填写实际检验结果，本表未预填合格结论','']])], '排产技术资料_待工艺复核_R'+str(state['revision'])))
    state['stage']='PRODUCTION_READY';stamp(state,'production_files_created')
    return {'state_json':pack(state),'quote_base64':quote['file_base64'],'quote_filename':quote['filename'],
            'production_base64':production['file_base64'],'production_filename':production['filename'],
            'message':full_status(state)+'\n已按确认的采购报价生成排产资料表格，请按企业工艺要求复核。'}


def export_archive(state_json):
    state=unpack(state_json);archive=deepcopy(state);archive.pop('sign_key',None);archive.pop('form',None)
    raw=j({'archive_version':1,'project':archive,'scope':'项目数据归档，原始附件存储在当前Dify实例中；不是审批凭证'}).encode()
    encoded=base64.b64encode(raw).decode()
    if len(encoded)>60000:raise ValueError('项目归档超过当前单文件传输预算')
    return {'file_base64':encoded,'filename':'项目数据归档_'+state['project_id']+'.json','message':'项目状态与确认记录已导出；原始附件继续由Dify保存。'}
