"""XLSX templates and signed return forms, using only Python's standard library."""
import base64
import gzip
import hashlib
import hmac
import io
import json
import re
import zipfile
from xml.sax.saxutils import escape,quoteattr
from .core import j,number


def norm(value):
    if value is None or str(value).strip() in ('nan','NaN','None'):return ''
    return ' '.join(str(value).replace('|','／').split()).strip()


def col(n):
    result=''
    while n:n,r=divmod(n-1,26);result=chr(65+r)+result
    return result


def safe(value):
    if isinstance(value,(dict,list)):value=j(value)
    text='' if value is None else str(value).replace('|','／')
    return "'"+text if text.startswith(('=','+','-','@')) else text


def sheet(name,headers,rows,editable=(),widths=None):
    return {'name':name,'headers':headers,'rows':rows,'editable':set(editable),'widths':widths or [24]*len(headers)}


def workbook(sheets,filename):
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    styles='''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="3"><font><sz val="10"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font><font><color rgb="FF17365D"/><sz val="10"/><name val="Microsoft YaHei"/></font></fonts><fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF234F70"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEAF1F7"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD0D8E0"/></left><right style="thin"><color rgb="FFD0D8E0"/></right><top style="thin"><color rgb="FFD0D8E0"/></top><bottom style="thin"><color rgb="FFD0D8E0"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="49" fontId="2" fillId="4" borderId="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="4" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    cells={};parts={};book=[];rels=[];types=[]
    for index,info in enumerate(sheets,1):
        name=info['name'];headers=info['headers'];rows=[headers]+info['rows']
        if len(info['rows'])>110 and name not in ('系统上下文','识别清单'):
            raise ValueError('表格超过110行，请按柜号分批，未输出截断表格')
        body=[]
        for rowno,row in enumerate(rows,1):
            if len(row)!=len(headers):raise ValueError('表格列数不一致：'+name)
            data=[]
            height=30 if rowno==1 else min(409,max(34,max((len(str(v or ''))*1.7/info['widths'][i]*14 for i,v in enumerate(row)),default=34)))
            for cno,value in enumerate(row,1):
                key=col(cno)+str(rowno);style=1 if rowno==1 else 3 if cno-1 in info['editable'] else 2 if rowno%2==0 else 0
                data.append('<c r="'+key+'" s="'+str(style)+'" t="inlineStr"><is><t xml:space="preserve"></t></is></c>')
                cells[name+'!'+key]=safe(value)
            body.append('<row r="'+str(rowno)+'" ht="'+str(round(height,1))+'" customHeight="1">'+''.join(data)+'</row>')
        widths=''.join('<col min="'+str(i)+'" max="'+str(i)+'" width="'+str(w)+'" customWidth="1"/>' for i,w in enumerate(info['widths'],1))
        parts['xl/worksheets/sheet'+str(index)+'.xml']='<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="'+ns+'"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:'+col(len(headers))+str(len(rows))+'"/><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="34"/><cols>'+widths+'</cols><sheetData>'+''.join(body)+'</sheetData><autoFilter ref="A1:'+col(len(headers))+str(len(rows))+'"/><pageMargins left="0.25" right="0.25" top="0.35" bottom="0.35" header="0.15" footer="0.15"/><pageSetup paperSize="8" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'
        book.append('<sheet name='+quoteattr(name)+' sheetId="'+str(index)+'" r:id="rId'+str(index)+'"'+(' state="veryHidden"' if name=='系统上下文' else '')+'/>')
        rels.append('<Relationship Id="rId'+str(index)+'" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet'+str(index)+'.xml"/>')
        types.append('<Override PartName="/xl/worksheets/sheet'+str(index)+'.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    parts['xl/styles.xml']=styles
    parts['xl/workbook.xml']='<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="'+ns+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(book)+'</sheets></workbook>'
    rels.append('<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    parts['xl/_rels/workbook.xml.rels']='<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(rels)+'</Relationships>'
    parts['_rels/.rels']='<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    parts['[Content_Types].xml']='<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(types)+'</Types>'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as output:
        for path,text in parts.items():output.writestr(path,text.encode())
    template=base64.b64encode(buffer.getvalue()).decode();payload=j({'cells':cells,'output_filename':filename})
    if len(template)>55000 or len(payload)>55000:raise ValueError('完整表格超出Dify节点预算，请拆分柜号')
    return {'template_base64':template,'payload_json':payload,'filename':filename}


def signed_sheets(state,kind,sheets):
    readonly=[]
    for info in sheets:
        for row in info['rows']:
            readonly.append({'type':row[0],'id':norm(row[1]),'values':{info['headers'][i]:norm(safe(value)) for i,value in enumerate(row) if i not in info['editable']}})
    packet={'project_id':state['project_id'],'revision':state['revision'],'kind':kind,'readonly':readonly}
    raw=j(packet).encode();signature=hmac.new(state['sign_key'].encode(),raw,hashlib.sha256).hexdigest()
    encoded=base64.b64encode(gzip.compress(raw,mtime=0)).decode()
    chunks=[encoded[i:i+18000] for i in range(0,len(encoded),18000)]
    meta=[['META','PART_'+str(i+1).zfill(4),value] for i,value in enumerate(chunks)]
    meta.append(['META','SIGNATURE',signature])
    sheets.append(sheet('系统上下文',['记录类型','行ID','值'],meta,widths=[18,25,100]))
    state['form']={'kind':kind,'revision':state['revision'],'signature':signature}
    return sheets


def recognition_rows(state):
    return [['PREVIEW',x['row_id'],x.get('cabinet_id',''),x.get('device_tag',''),x.get('role',''),x.get('shown_model',''),
        j(x.get('requirements',{})),x.get('quantity_per_cabinet'),x.get('unit',''),x.get('supply_scope',''),j(x.get('evidence',[]))] for x in state['facts']['items']]


def engineering_template(state):
    cabinets=[]
    for cab in state['facts']['cabinets']:
        layout=[];nodes=[]
        for item in state['facts']['items']:
            if item['cabinet_id']!=cab['cabinet_id']:continue
            q=number(item['quantity_per_cabinet'],True)
            if q==q.to_integral_value() and q<=100:
                for i in range(1,int(q)+1):
                    nodes.append({'row_id':item['row_id'],'instance':i,'x_mm':None,'y_mm':None})
                    layout.append(dict(nodes[-1],width_mm=None,height_mm=None))
        cabinets.append({'cabinet_id':cab['cabinet_id'],'width_mm':cab.get('width_mm'),'height_mm':cab.get('height_mm'),
            'depth_mm':cab.get('depth_mm'),'layout':layout,'schematic':nodes,'external_nodes':[],'wires':[],
            'wiring_reviewed':False,'non_layout_row_ids':[],'non_layout_reason':'','non_wired_row_ids':[]})
    return {'reviewed':False,'reviewer':'','revision':'A','cabinets':cabinets}


def form_workbook(state,markup='0.15',discount='0'):
    kind='ISSUES' if state['stage']=='NEEDS_CLARIFICATION' else 'BOM'
    sheets=[]
    if kind=='ISSUES':
        rows=[['ISSUE',x['id'],x['row_id'],x['message'],x['kind'],j(x.get('options',[])),'','',''] for x in state['issues']]
        sheets.append(sheet('问题确认',['记录类型','行ID','关联元件或柜号','问题说明','填写类型','参考信息','答复','确认','修订JSON'],rows,{6,7,8},[14,23,22,64,20,60,64,16,80]))
        sheets.append(sheet('识别清单',['记录类型','行ID','设备编号','回路位号','元件名称','资料型号','技术参数','单柜数量','单位','供货范围','来源定位','修订JSON'],
            [row+[''] for row in recognition_rows(state)],{11},widths=[14,18,24,20,24,48,65,16,14,26,80,90]))
        sheets.append(sheet('柜体修订',['记录类型','行ID','原柜体数量','原宽mm','原高mm','原深mm','修订JSON'],
            [['CAB',cab['cabinet_id'],cab.get('quantity'),cab.get('width_mm'),cab.get('height_mm'),cab.get('depth_mm'),''] for cab in state['facts']['cabinets']],{6}))
    else:
        rows=[];prices=[];changes=[]
        for r in state['bom']:
            rows.append(['BOM',r['row_id'],r['cabinet_id'],r['device_tag'],r['role'],r['description'],r['material_code'],r['manufacturer'],
                r['quantity_per_cabinet'],r['cabinet_quantity'],r['quantity'],r['unit'],r['supply_scope'],'',''])
            changes.append(['CHANGE',r['row_id'],''])
            if r['supply_scope']=='OUR_PURCHASE':
                refs=r.get('price_references',[])
                ref=refs[0] if len(refs)==1 and isinstance(refs[0],dict) else {}
                if ref and norm(ref.get('pricing_unit'))!=norm(r['unit']):ref={}
                price=r.get('price') or {}
                prices.append(['PRICE',r['row_id'],r['material_code'],r['unit'],j(refs),price.get('unit_price_ex_tax') if price.get('unit_price_ex_tax') is not None else ref.get('unit_price_ex_tax'),
                    price.get('tax_rate') if price.get('tax_rate') is not None else ref.get('tax_rate'),price.get('basis',''),''])
        sheets.append(sheet('BOM确认',['记录类型','行ID','设备编号','回路位号','元件名称','规格型号','物料编码','厂商名称','单柜数量','柜体数量','总数量','单位','供货范围','确认','修改意见'],rows,{13,14},[14,16,22,18,22,48,24,20,14,14,14,12,26,16,55]))
        sheets.append(sheet('价格确认',['记录类型','行ID','物料编码','计价单位','API参考价格(未审批)','确认未税采购单价','税率','价格依据','确认'],prices,{5,6,7,8},[14,18,24,16,70,26,18,60,16]))
        sheets.append(sheet('元件修订',['记录类型','行ID','修订JSON'],changes,{2},[14,18,120]))
        sheets.append(sheet('柜体修订',['记录类型','行ID','原柜体数量','原宽mm','原高mm','原深mm','修订JSON'],
            [['CAB',c['cabinet_id'],c.get('quantity'),c.get('width_mm'),c.get('height_mm'),c.get('depth_mm'),''] for c in state['facts']['cabinets']],{6},[14,24,20,20,20,20,100]))
        q=state.get('quote_settings') or {}
        sheets.append(sheet('报价设置',['记录类型','行ID','材料加成率','折扣率','确认','其他费用未税金额','其他费用税率','其他费用依据','费用确认'],
            [['SETTINGS','QUOTE',q.get('markup',markup),q.get('discount',discount),'',q.get('fees',0),q.get('fees_tax',0),q.get('fees_basis',''),'']],{2,3,4,5,6,7,8}))
        engineering=state.get('engineering') or engineering_template(state)
        if len(j(engineering))>30000:raise ValueError('CAD工程参数超过Excel单格容量，请拆分柜号')
        sheets.append(sheet('CAD工程参数',['记录类型','行ID','工程JSON'],[['CAD','ENGINEERING',j(engineering)]],{2},[14,22,150]))
    sheets.append(sheet('填写说明',['记录类型','行ID','项目名称','项目ID','版本','确认人','填写说明'],
        [['SETTINGS','APPROVER',state['name'],state['project_id'],state['revision'],'',
          '只填黄色格。确认填是；数字填实际值。范围用OUR_PURCHASE/CUSTOMER_SUPPLIED/UTILITY_SUPPLIED/RESERVED。识别清单可填修订JSON；删除重复项写{"exclude":true,"reason":"具体剔除依据"}，不可直接删行。修订后需再核对新版清单。价格和CAD未确认时只阻止对应输出。务必在同一Dify对话回传最新表。']],{5},[14,22,35,40,14,24,100]))
    sheets=signed_sheets(state,kind,sheets)
    return workbook(sheets,('问题确认单' if kind=='ISSUES' else 'BOM确认单')+'_V20_'+str(state['revision']))


def parse_markdown(text):
    records={};headers=None
    for line in str(text or '').splitlines():
        if not line.strip().startswith('|'):continue
        cells=[x.strip() for x in line.strip()[1:-1].split('|')]
        if cells and cells[0]=='记录类型':headers=cells;continue
        if not headers or not cells or re.fullmatch(r':?-+:?',cells[0]):continue
        if cells[0] not in ('ISSUE','BOM','PRICE','CHANGE','CAB','SETTINGS','CAD','META','PREVIEW','SOURCE','SOURCEADD','QUOTE','TECH'):continue
        if len(cells)!=len(headers):raise ValueError('确认表含分隔符或结构变化，请勿在单元格中填写竖线符号')
        row={k:norm(v) for k,v in zip(headers,cells)};key=(row['记录类型'],row['行ID'])
        if key in records:raise ValueError('确认表行ID重复，不能关联答复')
        records[key]=row
    return records


def equal_cell(a,b):
    if norm(a)==norm(b):return True
    try:return number(a)==number(b)
    except ValueError:return False


def parse_form(state,text,kind):
    records=parse_markdown(text)
    pieces=sorted((rid,row['值']) for (typ,rid),row in records.items() if typ=='META' and rid.startswith('PART_'))
    if [rid for rid,_ in pieces]!=['PART_'+str(i).zfill(4) for i in range(1,len(pieces)+1)] or not pieces:
        raise ValueError('系统上下文缺段；请使用本项目最新确认表')
    encoded=''.join(value for _,value in pieces)
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded,validate=True))) as stream:raw=stream.read(700001)
    if len(raw)>700000:raise ValueError('确认表上下文超出容量')
    packet=json.loads(raw);signature=records.get(('META','SIGNATURE'),{}).get('值','')
    expected=hmac.new(state['sign_key'].encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):raise ValueError('确认单签名无效，固定数据可能被改动')
    if packet['project_id']!=state['project_id'] or packet['kind']!=kind:raise ValueError('确认表所属项目或类型不匹配')
    if packet['revision']!=state['revision']:raise ValueError('确认表已过期，请回传最新版本')
    readonly=packet['readonly'];allowed={(x['type'],x['id']) for x in readonly}
    if {(t,r) for t,r in records if t!='META'}!=allowed:raise ValueError('确认表行数/行ID发生变化，请下载最新表重填')
    for record in readonly:
        row=records[(record['type'],record['id'])]
        for key,value in record['values'].items():
            if key not in row or not equal_cell(row[key],value):raise ValueError('固定字段被改动：'+key+'；请在修订JSON说明变更')
    approver=records.get(('SETTINGS','APPROVER'),{}).get('确认人','')
    if len(approver)<2:raise ValueError('请在填写说明中填写实际确认人')
    return records,approver
