from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path

import ezdxf
import httpx
from docx import Document
from openpyxl import Workbook

from .core import Problem, canonical, number, now
from .forms import add_sheet, SCOPE_LABELS


def money(value):
    return number(value).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def quote_rows(project):
    settings=project.get('quote_settings',{})
    if not settings.get('confirmed'):
        raise Problem('QUOTE_SETTINGS_UNCONFIRMED','报价加成率、折扣和费用尚未由工程师确认。')
    markup=number(settings.get('markup'));discount=number(settings.get('discount'))
    if not 0<=markup<=10 or not 0<=discount<1:
        raise Problem('QUOTE_SETTINGS_INVALID','加成率范围0至10，折扣率范围0至小于1。')
    result=[]
    for row in project['bom']:
        if row['supply_scope']!='OUR_PURCHASE':continue
        price=row['price']
        if not price.get('confirmed') or price.get('reference_only') or not price.get('basis'):
            raise Problem('PRICE_UNCONFIRMED','存在未确认的采购价格；参考/估算价不能直接生成正式报价。')
        cost=number(price.get('unit_price_ex_tax'));tax=number(price.get('tax_rate'))
        if cost<0 or not 0<=tax<=1:
            raise Problem('PRICE_INVALID','确认采购单价不得为负数，税率范围0至1。')
        unit=money(cost*(1+markup)*(1-discount))
        net=money(unit*number(row['quantity']));tax_amount=money(net*tax)
        result.append([row['cabinet_id'],row['device_tag'],row['description'],row['material_code'],row['manufacturer'],
            row['quantity'],row['unit'],float(unit),float(tax),float(net),float(tax_amount),float(net+tax_amount),price['basis']])
    fees=number(settings.get('fees') or 0);fees_tax=number(settings.get('fees_tax') or 0)
    if fees<0 or not 0<=fees_tax<=1:
        raise Problem('FEES_INVALID','其他费用不得为负，税率范围0至1。')
    if fees:
        if not settings.get('fees_confirmed') or not settings.get('fees_basis'):
            raise Problem('FEES_UNCONFIRMED','请确认其他费用及其依据。')
        net=money(fees);ta=money(net*fees_tax)
        result.append(['项目费用','','其他费用','','',1,'项',float(net),float(fees_tax),float(net),float(ta),float(net+ta),settings['fees_basis']])
    return result


def write_quote(project,path):
    rows=quote_rows(project)
    wb=Workbook();wb.remove(wb.active)
    add_sheet(wb,'报价明细',['设备编号','回路/位号','元器件名称及规格','物料编码','厂商','数量','单位','未税销售单价','税率','未税金额','税额','含税金额','价格依据'],rows,
              [24,20,52,24,20,14,12,22,14,22,22,22,45])
    net=sum((money(r[9]) for r in rows),Decimal(0));tax=sum((money(r[10]) for r in rows),Decimal(0))
    add_sheet(wb,'报价汇总',['项目名称','项目ID','BOM版本','未税合计','税额合计','含税合计','确认人'],
              [[project['name'],project['id'],project['revision'],float(net),float(tax),float(net+tax),project['quote_settings']['approver']]])
    excluded=[(r['cabinet_id'],r['device_tag'],r['description'],SCOPE_LABELS.get(r['supply_scope']),r['quantity'],r['unit']) for r in project['bom'] if r['supply_scope']!='OUR_PURCHASE']
    add_sheet(wb,'甲供与预留(不计价)',['设备编号','位号','名称规格','供货范围','数量','单位'],excluded)
    wb.save(path);return path


def write_procurement(project,path):
    wb=Workbook();wb.remove(wb.active)
    headers=['设备编号','回路/位号','名称','规格型号','物料编码','厂商名称','单柜数量','柜体数量','总数量','单位','供货范围','工程确认人','确认版本']
    for scope,title in [('OUR_PURCHASE','元器件采购清单'),('OTHER','甲供与预留')]:
        rows=[]
        for r in project['bom']:
            if (r['supply_scope']=='OUR_PURCHASE')!=(scope=='OUR_PURCHASE'):continue
            rows.append([r['cabinet_id'],r['device_tag'],r['role'],r['description'],r['material_code'],r['manufacturer'],r['quantity_per_cabinet'],r['cabinet_quantity'],r['quantity'],r['unit'],SCOPE_LABELS.get(r['supply_scope']),r['approval'],project['revision']])
        add_sheet(wb,title,headers,rows,[24,20,24,52,24,22,14,14,14,12,24,22,14])
    wb.save(path);return path


def write_production(project,path):
    doc=Document();doc.add_heading(project['name']+'—排产技术资料',0)
    doc.add_paragraph(f"项目 {project['id']}；BOM确认版本 {project['revision']}；生成时间 {now()}。")
    doc.add_paragraph('本资料依据已确认BOM和客户资料生成。机械开孔、导线截面、温升、短路耐受、保护配合、接地及出厂试验应按适用标准由工程师校核。未提供的参数不按经验补齐。')
    doc.add_heading('柜体要求',1)
    for cab in project['facts']['cabinets']:
        doc.add_paragraph('设备编号：'+cab['cabinet_id']+'；数量：'+str(cab.get('quantity')))
        doc.add_paragraph('宽×高×深(mm)：'+' × '.join(str(cab.get(k)) if cab.get(k) is not None else '待工艺确认' for k in ('width_mm','height_mm','depth_mm'))+
            '；板厚(mm)：'+str(cab.get('thickness_mm') or '按已确认工艺要求')+'；防护等级：'+str(cab.get('ip_grade') or '待工艺确认'))
        for note in cab.get('notes',[]):doc.add_paragraph(str(note))
    doc.add_heading('关键技术要求与确认结论',1)
    for note in project['facts'].get('notes',[]):
        doc.add_paragraph(note['text']+'；确认：'+str(note.get('confirmation') or '来自资料'))
    for text in project['facts'].get('engineer_notes',[]):doc.add_paragraph(text)
    doc.add_heading('已确认BOM',1)
    table=doc.add_table(rows=1, cols=6);table.style='Light Shading Accent 1'
    for cell,label in zip(table.rows[0].cells,['设备/位号','名称规格','编码','数量/单位','供货范围','确认人']):cell.text=label
    for r in project['bom']:
        values=[r['cabinet_id']+'/'+r['device_tag'],r['description'],r['material_code'],str(r['quantity'])+r['unit'],SCOPE_LABELS.get(r['supply_scope'],r['supply_scope']),r['approval']]
        for cell,value in zip(table.add_row().cells,values):cell.text=str(value)
    doc.add_heading('CAD状态与工艺约束',1)
    doc.add_paragraph('自动生成范围为按已确认工程坐标绘制的元件布置和接线关系图。柜体结构展开、母排加工、安装孔、复杂控制原理等需要企业图框和工艺规则扩展。DWG转换成功仅证明文件格式，不替代电气工程校核。')
    engineering=project.get('engineering',{})
    doc.add_paragraph('工程参数校核人：'+str(engineering.get('reviewer') or '尚未填写')+'；工程参数版本：'+str(engineering.get('revision') or '尚未填写'))
    doc.save(path);return path


def node_id(row_id,instance,rows):
    return row_id if number(rows[row_id]['quantity_per_cabinet'])==1 else row_id+'@'+str(instance)


def validate_engineering(project):
    data=project.get('engineering') or {}
    if data.get('reviewed') is not True or not str(data.get('reviewer') or '').strip():
        raise Problem('CAD_ENGINEERING_UNCONFIRMED','CAD工程参数需填写实际坐标、尺寸、端子、导线规格和校核人，再将reviewed设为true。')
    cabinets={x['cabinet_id'] for x in project['facts']['cabinets']}
    data_cabs=data.get('cabinets') or []
    if len(data_cabs)!=len(cabinets) or {x.get('cabinet_id') for x in data_cabs}!=cabinets:
        raise Problem('CAD_CABINETS_INVALID','CAD柜体编号必须与已确认BOM一致。')
    for cab in data_cabs:
        w=number(cab.get('width_mm'),positive=True);h=number(cab.get('height_mm'),positive=True)
        number(cab.get('depth_mm'),positive=True)
        source=next(c for c in project['facts']['cabinets'] if c['cabinet_id']==cab['cabinet_id'])
        for field in ('width_mm','height_mm','depth_mm'):
            if source.get(field) not in (None,'') and number(source[field])!=number(cab[field]):
                raise Problem('CAD_DIMENSIONS_MISMATCH','CAD尺寸与BOM柜体资料不一致，请通过“柜体修订”明确改动并重新确认。')
        rows={r['row_id']:r for r in project['bom'] if r['cabinet_id']==cab['cabinet_id']}
        expected=set(rows)
        omitted=set(cab.get('non_layout_row_ids') or [])
        unwired=set(cab.get('non_wired_row_ids') or [])
        if not omitted<=expected or not unwired<=expected or (omitted and not cab.get('non_layout_reason')):
            raise Problem('CAD_EXCLUSION_INVALID','布置或接线排除项必须是已确认元件，并填写不单独布置的理由。')
        instance_keys=set()
        for rid,row in rows.items():
            q=number(row['quantity_per_cabinet'],positive=True)
            if rid in omitted and rid in unwired:continue
            if q!=q.to_integral_value() or q>100:
                raise Problem('CAD_QUANTITY_NOT_DISCRETE','米/公斤等耗材需工程师明确不单独布置/接线；单项超过100件请拆分。')
            instance_keys|={(rid,i) for i in range(1,int(q)+1)}
        layout=cab.get('layout') or [];schematic=cab.get('schematic') or []
        for values,exclusions in ((layout,omitted),(schematic,unwired)):
            required={k for k in instance_keys if k[0] not in exclusions}
            if len(values)!=len(required) or {(v.get('row_id'),v.get('instance',1)) for v in values}!=required:
                raise Problem('CAD_ITEMS_INCOMPLETE','CAD布置与接线节点必须覆盖所有已确认元件。')
        rectangles=[]
        for box in layout:
            x=number(box.get('x_mm'));y=number(box.get('y_mm'))
            bw=number(box.get('width_mm'),positive=True);bh=number(box.get('height_mm'),positive=True)
            if x<0 or y<0 or x+bw>w or y+bh>h:
                raise Problem('CAD_LAYOUT_OUTSIDE','元件尺寸或坐标超出柜体边界。')
            if any(x<rx+rw and x+bw>rx and y<ry+rh and y+bh>ry for rx,ry,rw,rh in rectangles):
                raise Problem('CAD_LAYOUT_OVERLAP','元件布置矩形存在重叠，请核对坐标。')
            rectangles.append((x,y,bw,bh))
        nodes={}
        for node in schematic:
            x=number(node.get('x_mm'));y=number(node.get('y_mm'))
            if (x,y) in nodes.values():raise Problem('CAD_NODE_OVERLAP','接线节点坐标重复。')
            nodes[node_id(node['row_id'],node.get('instance',1),rows)]=(x,y)
        for node in cab.get('external_nodes') or []:
            nid=node.get('id','')
            if not nid.startswith('EXT_') or nid in nodes or not node.get('label'):
                raise Problem('CAD_EXTERNAL_NODE_INVALID','外部节点需要唯一EXT_编号和名称。')
            nodes[nid]=(number(node.get('x_mm')),number(node.get('y_mm')))
        wires=cab.get('wires') or []
        if cab.get('wiring_reviewed') is not True or not wires:
            raise Problem('CAD_WIRING_UNCONFIRMED','请填写接线关系和外部进出线节点，并确认wiring_reviewed。')
        connected=set()
        for wire in wires:
            if wire.get('from') not in nodes or wire.get('to') not in nodes or wire['from']==wire['to']:
                raise Problem('CAD_WIRE_ENDPOINT_INVALID','接线端点必须是该柜的已知节点。')
            if not all(str(wire.get(k) or '').strip() for k in ('from_terminal','to_terminal','wire_label','conductor_spec')):
                raise Problem('CAD_WIRE_DETAILS_MISSING','每条接线均需端子、线号和导线规格。')
            connected|={wire['from'],wire['to']}
        # Explicitly reviewed reserved/mechanical items may be excluded from wiring.
        required_nodes={node_id(rid,i,rows) for rid,i in instance_keys if rid not in unwired}
        if required_nodes-connected:
            raise Problem('CAD_WIRING_INCOMPLETE','存在未接线元件；非电气或预留项请明确加入non_wired_row_ids。')
    return data


def write_dxf(project,path):
    data=validate_engineering(project)
    doc=ezdxf.new('R2018');doc.units=4
    for name,color in [('CABINET',7),('COMPONENT',3),('WIRING',1),('LABEL',2)]:doc.layers.new(name,dxfattribs={'color':color})
    ms=doc.modelspace();offset=0.0
    rows={x['row_id']:x for x in project['bom']}
    def label(text,x,y,height=4):
        # MTEXT syntax is not accepted from uploaded descriptions.
        text=str(text).replace('\\','/').replace('{','(').replace('}',')').replace('\n',' ')
        ms.add_mtext(text,dxfattribs={'insert':(x,y),'char_height':height,'width':180,'layer':'LABEL'})
    for cab in data['cabinets']:
        w=float(number(cab['width_mm']));h=float(number(cab['height_mm']))
        ms.add_lwpolyline([(offset,0),(offset+w,0),(offset+w,h),(offset,h)],close=True,dxfattribs={'layer':'CABINET'})
        label(f"{cab['cabinet_id']}  {w:g} x {h:g} x {cab['depth_mm']} mm",offset,h+30,8)
        for box in cab['layout']:
            x=offset+float(number(box['x_mm']));y=float(number(box['y_mm']));bw=float(number(box['width_mm']));bh=float(number(box['height_mm']))
            ms.add_lwpolyline([(x,y),(x+bw,y),(x+bw,y+bh),(x,y+bh)],close=True,dxfattribs={'layer':'COMPONENT'})
            r=rows[box['row_id']];label(r['device_tag']+' ['+str(box.get('instance',1))+'] '+r['description'],x,y+bh,4)
        sx=offset+w+250;nodes={}
        for node in cab['schematic']:
            rid=node['row_id'];nid=node_id(rid,node.get('instance',1),rows)
            x=sx+float(number(node['x_mm']));y=float(number(node['y_mm']));nodes[nid]=(x,y)
            ms.add_circle((x,y),5,dxfattribs={'layer':'COMPONENT'});label(rows[rid]['device_tag']+' '+nid,x+8,y+8)
        for node in cab.get('external_nodes') or []:
            x=sx+float(number(node['x_mm']));y=float(number(node['y_mm']));nodes[node['id']]=(x,y)
            ms.add_circle((x,y),3,dxfattribs={'layer':'COMPONENT'});label(node['label'],x+8,y+8)
        for wire in cab['wires']:
            a=nodes[wire['from']];b=nodes[wire['to']]
            ms.add_line(a,b,dxfattribs={'layer':'WIRING'})
            label(f"{wire['wire_label']} {wire['conductor_spec']} {wire['from_terminal']}->{wire['to_terminal']}",(a[0]+b[0])/2,(a[1]+b[1])/2)
        label('接线关系示意；非按比例电气符号图。工程校核：'+data['reviewer'],offset,-30,5)
        offset=max([x for x,y in nodes.values()]+[offset+w])+400
    doc.saveas(path);return path


class CadClient:
    def __init__(self,settings):self.settings=settings

    def convert(self,path,target):
        if not self.settings.cad_url:
            raise Problem('CAD_CONVERTER_NOT_CONFIGURED','尚未配置CAD转换服务，不能交付DWG。')
        try:
            with httpx.Client(timeout=90,follow_redirects=False) as client,open(path,'rb') as stream:
                response=client.post(self.settings.cad_url,headers={'X-API-Key':self.settings.cad_key},
                    files={'file':(Path(path).name,stream,'application/dxf')})
            if response.status_code!=200:
                raise Problem('CAD_CONVERSION_FAILED','CAD转换服务未成功返回DWG，请检查转换服务。',502)
            content=response.content
            if len(content)<128 or len(content)>64*1024*1024 or not content[:6].startswith(b'AC10'):
                raise Problem('CAD_OUTPUT_INVALID','转换结果不是可识别的DWG文件头；未伪装DXF交付。',502)
            Path(target).write_bytes(content)
            return target
        except Problem:raise
        except Exception:
            raise Problem('CAD_CONVERSION_FAILED','连接CAD转换服务失败。',502) from None
