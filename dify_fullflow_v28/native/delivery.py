"""Delivery calculations and ASCII DXF geometry run inside Dify code nodes."""
from .core import *
from .excel import workbook,sheet,norm


def money(value):return number(value).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def quotation(state):
    settings=state.get('quote_settings') or {}
    if not settings.get('confirmed'):raise ValueError('加成率与折扣尚未确认')
    markup=number(settings.get('markup'));discount=number(settings.get('discount'))
    if not 0<=markup<=10 or not 0<=discount<1:raise ValueError('加成率范围0至10，折扣率范围0至小于1')
    rows=[]
    for row in state['bom']:
        if row['supply_scope']!='OUR_PURCHASE':continue
        price=row['price']
        if not price.get('confirmed') or price.get('reference_only') or not price.get('basis'):raise ValueError('存在尚未确认的采购价/价格依据，不能把参考估算价当正式报价')
        if re.search(r'估算|估价|演示|模拟|占位|REFERENCE[_ -]?(?:ONLY|ESTIMATE)|DEMO',str(price['basis']),re.I):
            raise ValueError('价格依据仍标注为估算/演示，需正式核准的采购价依据后才生成正式报价')
        cost=number(price.get('unit_price_ex_tax'));tax=number(price.get('tax_rate'))
        if cost<0 or not 0<=tax<=1:raise ValueError('未税采购单价不得为负，税率范围0至1')
        unit=money(cost*(1+markup)*(1-discount));net=money(unit*number(row['quantity']));ta=money(net*tax)
        rows.append(['QUOTE',row['row_id'],row['cabinet_id'],row['device_tag'],row['description'],row['material_code'],row['manufacturer'],
            row['quantity'],row['unit'],float(unit),float(tax),float(net),float(ta),float(net+ta),price['basis']])
    fees=number(settings.get('fees') or 0);tax=number(settings.get('fees_tax') or 0)
    if fees<0 or not 0<=tax<=1:raise ValueError('其他费用金额或税率不正确')
    if fees:
        if not settings.get('fees_confirmed') or not settings.get('fees_basis'):raise ValueError('其他费用尚未确认')
        net=money(fees);ta=money(net*tax)
        rows.append(['QUOTE','FEES','项目费用','','其他费用','','',1,'项',float(net),float(tax),float(net),float(ta),float(net+ta),settings['fees_basis']])
    net=sum((money(x[11]) for x in rows),Decimal(0));ta=sum((money(x[12]) for x in rows),Decimal(0))
    return rows,float(net),float(ta),float(net+ta)


def delivery_books(state):
    if state['stage'] not in ('CONFIRMED','DELIVERED') or not state['bom'] or not all(r.get('approval') for r in state['bom']):raise ValueError('必须先完成全部问题与BOM逐项确认')
    if hashlib.sha256(j(state['bom']).encode()).hexdigest()!=state.get('confirmed_bom_hash'):raise ValueError('已确认BOM内容变化，请重新确认')
    headers=['记录类型','行ID','设备编号','回路位号','元件名称','规格型号','物料编码','厂商名称','单柜数量','柜体数量','总数量','单位','供货范围','确认人']
    purchased=[];excluded=[]
    for r in state['bom']:
        line=['PURCHASE',r['row_id'],r['cabinet_id'],r['device_tag'],r['role'],r['description'],r['material_code'],r['manufacturer'],
            r['quantity_per_cabinet'],r['cabinet_quantity'],r['quantity'],r['unit'],SCOPE_NAMES[r['supply_scope']],r['approval']]
        (purchased if r['supply_scope']=='OUR_PURCHASE' else excluded).append(line)
    summary=sheet('项目与版本',['项目名称','项目ID','BOM版本','工程确认人'],[[state['name'],state['project_id'],state['revision'],state['bom'][0]['approval']]])
    procurement=workbook([summary,sheet('元器件采购清单',headers,purchased,widths=[14,16,22,18,22,48,24,20,14,14,14,12,24,22]),sheet('甲供与预留',headers,excluded)],'正式元器件采购清单_V20')
    cabs=[['CABINET',c['cabinet_id'],c.get('quantity'),c.get('width_mm'),c.get('height_mm'),c.get('depth_mm'),c.get('thickness_mm'),c.get('ip_grade',''),j(c.get('notes',[]))] for c in state['facts']['cabinets']]
    notes=[['NOTE','N'+str(i+1),x.get('text',''),x.get('confirmation','来自资料'),x.get('approver',''),j(x.get('evidence',[]))] for i,x in enumerate(state['facts']['notes'])]
    notes.append(['NOTE','CAD_SCOPE','CAD自动绘制范围为工程师确认尺寸与坐标的元件布置及接线关系节点示意。企业图例、钣金、母排、开孔、温升/短路耐受及保护配合需另行校核。','待企业工艺与审图流程完成','',''])
    production=workbook([summary,sheet('柜体技术要求',['记录类型','柜号','柜体数量','宽mm','高mm','深mm','板厚mm','防护等级','技术备注'],cabs),
        sheet('关键技术要求',['记录类型','编号','要求','工程确认结论','确认人','来源'],notes,widths=[14,18,90,75,24,90]),
        sheet('已确认物料',headers,purchased+excluded),
        sheet('工艺与检验记录',['项目','要求或核对内容','责任人记录'],[['结构工艺','依据确认尺寸与企业柜型规范核对结构、开孔和安装位置',''],['电气校核','核对接线、端子、线径、接地、保护配合及适用标准',''],['出厂检验','按企业适用规范记录检验结果；本表未预填合格结论','']])],'排产技术资料_V20')
    error='';quote={'template_base64':'','payload_json':'{}','filename':''}
    try:
        rows,net,tax,total=quotation(state)
        quote=workbook([summary,sheet('报价明细',['记录类型','行ID','设备编号','回路位号','名称规格','物料编码','厂商','数量','单位','未税销售单价','税率','未税金额','税额','含税金额','价格依据'],rows,widths=[14,18,22,18,50,24,20,14,12,24,16,24,24,24,50]),
            sheet('报价汇总',['未税合计','税额合计','含税合计','工程确认人'],[[net,tax,total,state['quote_settings']['approver']]]),sheet('甲供与预留(不计价)',headers,excluded)],'正式报价文件_V20')
    except ValueError as exc:error=str(exc)
    return procurement,production,quote,error


def node_key(rid,instance,rows):
    return rid if number(rows[rid]['quantity_per_cabinet'])==1 else rid+'@'+str(instance)


def cad_dxf(state):
    data=state.get('engineering') or {}
    if data.get('reviewed') is not True or len(str(data.get('reviewer') or ''))<2:raise ValueError('CAD工程参数未校核，需填写实际尺寸、坐标、端子、导线和校核人')
    sources={c['cabinet_id']:c for c in state['facts']['cabinets']};cabinets=data.get('cabinets') or []
    if len(cabinets)!=len(sources) or {c.get('cabinet_id') for c in cabinets}!=set(sources):raise ValueError('CAD柜号必须与确认BOM一致')
    out=[]
    def put(*pairs):
        for code,value in pairs:out.extend([str(code),str(value)])
    def text(value,x,y,height=4):
        value=norm(value).replace('\\','/').replace('{','(').replace('}',')')
        put((0,'TEXT'),(8,'LABEL'),(10,x),(20,y),(30,0),(40,height),(1,value))
    def line(a,b,layer='WIRING'):
        put((0,'LINE'),(8,layer),(10,a[0]),(20,a[1]),(30,0),(11,b[0]),(21,b[1]),(31,0))
    def rectangle(x,y,w,h,layer):
        pts=[(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
        for a,b in zip(pts,pts[1:]):line(a,b,layer)
    put((0,'SECTION'),(2,'HEADER'),(9,'$ACADVER'),(1,'AC1024'),(9,'$INSUNITS'),(70,4),(0,'ENDSEC'))
    put((0,'SECTION'),(2,'TABLES'),(0,'TABLE'),(2,'LAYER'),(70,4))
    for name,color in [('CABINET',7),('COMPONENT',3),('WIRING',1),('LABEL',2)]:put((0,'LAYER'),(2,name),(70,0),(62,color),(6,'CONTINUOUS'))
    put((0,'ENDTAB'),(0,'ENDSEC'),(0,'SECTION'),(2,'ENTITIES'));offset=0.0
    for cab in cabinets:
        cid=cab['cabinet_id'];w=float(number(cab.get('width_mm'),True));h=float(number(cab.get('height_mm'),True));number(cab.get('depth_mm'),True)
        for k in ('width_mm','height_mm','depth_mm'):
            if sources[cid].get(k) not in (None,'') and number(sources[cid][k])!=number(cab[k]):raise ValueError('CAD尺寸与确认柜体资料冲突，请在柜体修订中明确修改')
        rows={r['row_id']:r for r in state['bom'] if r['cabinet_id']==cid};ids=set(rows)
        omitted=set(cab.get('non_layout_row_ids') or []);unwired=set(cab.get('non_wired_row_ids') or [])
        if not omitted<=ids or not unwired<=ids or (omitted and not cab.get('non_layout_reason')):raise ValueError('CAD排除项需要真实元件ID和不单独布局的理由')
        instances=set()
        for rid,row in rows.items():
            if rid in omitted and rid in unwired:continue
            q=number(row['quantity_per_cabinet'],True)
            if q!=q.to_integral_value() or q>100:raise ValueError('按米/公斤计量的耗材需明确排除独立实例，或拆分超大数量')
            instances|={(rid,i) for i in range(1,int(q)+1)}
        layout=cab.get('layout') or [];schematic=cab.get('schematic') or []
        for values,exclusions in ((layout,omitted),(schematic,unwired)):
            required={x for x in instances if x[0] not in exclusions}
            if len(values)!=len(required) or {(x.get('row_id'),x.get('instance',1)) for x in values}!=required:raise ValueError('CAD必须覆盖每个单柜元件实例；不能多个器件只画一个')
        rectangle(offset,0,w,h,'CABINET');text(cid+' '+str(w)+'x'+str(h)+'x'+str(cab['depth_mm'])+'mm',offset,h+25,7)
        occupied=[]
        for box in layout:
            x=float(number(box.get('x_mm')));y=float(number(box.get('y_mm')));bw=float(number(box.get('width_mm'),True));bh=float(number(box.get('height_mm'),True))
            if x<0 or y<0 or x+bw>w or y+bh>h:raise ValueError('元件布置超出柜体边界')
            if any(x<a+c and x+bw>a and y<b+d and y+bh>b for a,b,c,d in occupied):raise ValueError('元件布置矩形重叠')
            occupied.append((x,y,bw,bh));rectangle(offset+x,y,bw,bh,'COMPONENT')
            r=rows[box['row_id']];text(r['device_tag']+' ['+str(box.get('instance',1))+'] '+r['description'],offset+x,y+bh)
        nodes={};sx=offset+w+250
        for row in schematic:
            key=node_key(row['row_id'],row.get('instance',1),rows);point=(sx+float(number(row.get('x_mm'))),float(number(row.get('y_mm'))))
            if point in nodes.values():raise ValueError('接线节点坐标重叠')
            nodes[key]=point;put((0,'CIRCLE'),(8,'COMPONENT'),(10,point[0]),(20,point[1]),(30,0),(40,4));text(key,point[0]+7,point[1]+7)
        for row in cab.get('external_nodes') or []:
            key=row.get('id','')
            if not key.startswith('EXT_') or key in nodes or not row.get('label'):raise ValueError('外部节点须有唯一EXT_编号和名称')
            nodes[key]=(sx+float(number(row.get('x_mm'))),float(number(row.get('y_mm'))));text(row['label'],nodes[key][0]+7,nodes[key][1]+7)
        wires=cab.get('wires') or [];connected=set()
        if cab.get('wiring_reviewed') is not True or not wires:raise ValueError('需要确认接线关系、外部进出线节点和导线规格')
        for wire in wires:
            if wire.get('from') not in nodes or wire.get('to') not in nodes or wire['from']==wire['to']:raise ValueError('接线端点必须是该柜的已知实例')
            if not all(wire.get(k) for k in ('from_terminal','to_terminal','wire_label','conductor_spec')):raise ValueError('每条接线需填写端子、线号和导线规格')
            a=nodes[wire['from']];b=nodes[wire['to']];line(a,b)
            text(str(wire['wire_label'])+' '+str(wire['conductor_spec'])+' '+str(wire['from_terminal'])+'->'+str(wire['to_terminal']),(a[0]+b[0])/2,(a[1]+b[1])/2)
            connected|={wire['from'],wire['to']}
        required_nodes={node_key(rid,i,rows) for rid,i in instances if rid not in unwired}
        if required_nodes-connected:raise ValueError('仍有电气实例未接线；非电气件应明确列入non_wired_row_ids')
        text('接线关系示意；工程校核：'+data['reviewer'],offset,-30)
        offset=max([x for x,y in nodes.values()]+[offset+w])+400
    put((0,'ENDSEC'),(0,'EOF'));data=('\n'.join(out)+'\n').encode('utf-8')
    encoded=base64.b64encode(data).decode()
    if len(encoded)>48000:raise ValueError('工程图超过本版单次转换请求预算，请按柜号拆分')
    return encoded
