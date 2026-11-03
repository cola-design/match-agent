"""Signed engineer return forms. Editable cells are explicit; all other cells are checked."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .core import Problem, canonical, number

YES = {'是','确认','已确认','yes','YES'}
SCOPE_LABELS = {'OUR_PURCHASE':'我方采购','CUSTOMER_SUPPLIED':'甲供','UTILITY_SUPPLIED':'供电局供货','RESERVED':'仅预留'}


def cell_value(value):
    if isinstance(value, (dict,list)):
        return canonical(value)
    return value


def add_sheet(wb, name, headers, rows, widths=None):
    ws = wb.create_sheet(name)
    ws.append(headers)
    for row in rows:
        ws.append([cell_value(v) for v in row])
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
    for row in ws:
        for cell in row:
            # Never let customer text become an executable Excel formula.
            if cell.data_type == 'f':
                cell.data_type = 's'
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            if cell.row == 1:
                cell.fill = PatternFill('solid', fgColor='234F70')
                cell.font = Font(name='微软雅黑',color='FFFFFF',bold=True)
            else:
                cell.font = Font(name='微软雅黑',size=10)
                if cell.row % 2 == 0:
                    cell.fill = PatternFill('solid',fgColor='EAF1F7')
    ws.row_dimensions[1].height = 32
    for i, col in enumerate(ws.iter_cols(),1):
        ws.column_dimensions[col[0].column_letter].width = (widths[i-1] if widths and i<=len(widths) else 22)
    return ws


def sign_book(wb, store, project, kind, editable, path):
    readonly = []
    for ws in wb:
        for row in ws:
            for cell in row:
                key = ws.title+'!'+cell.coordinate
                if key not in editable:
                    readonly.append([ws.title,cell.coordinate,None if cell.value=='' else cell.value])
                else:
                    cell.fill = PatternFill('solid',fgColor='FFF2CC')
    context = {'project_id':project['id'],'revision':project['revision'],'kind':kind,
               'editable':sorted(editable),'readonly':readonly,
               'shapes':{ws.title:[ws.max_row,ws.max_column] for ws in wb}}
    hidden = wb.create_sheet('_context')
    raw = canonical(context)
    # Excel cell strings have a 32767 character limit; chunk the signed manifest.
    hidden.append([store.signature(context)])
    for offset in range(0,len(raw),28000):
        hidden.append([raw[offset:offset+28000]])
    hidden.sheet_state = 'veryHidden'
    wb.save(path)
    return Path(path)


def read_form(path, store, project, kind):
    try:
        with zipfile.ZipFile(path) as z:
            if sum(x.file_size for x in z.infolist()) > 24*1024*1024 or len(z.infolist())>100:
                raise ValueError()
        wb = load_workbook(path, data_only=False, keep_links=False)
        hidden = wb['_context']
        context = json.loads(''.join(str(hidden.cell(i,1).value or '') for i in range(2,hidden.max_row+1)))
        import hmac
        if not hmac.compare_digest(str(hidden['A1'].value),store.signature(context)):
            raise ValueError()
    except Exception:
        raise Problem('FORM_INVALID','确认表无效或签名被修改，请使用本项目下载的原始Excel。',422) from None
    if context['project_id']!=project['id'] or context['kind']!=kind:
        raise Problem('FORM_PROJECT_MISMATCH','确认表类型或所属项目不正确。',422)
    if context['revision']!=project['revision']:
        raise Problem('FORM_STALE','该确认表已过期，请下载当前项目最新版本。',422)
    for title,shape in context['shapes'].items():
        if title not in wb.sheetnames or [wb[title].max_row,wb[title].max_column]!=shape:
            raise Problem('FORM_STRUCTURE_CHANGED','请勿增加/删除行列；只填写黄色单元格。',422)
    for title,coordinate,value in context['readonly']:
        if wb[title][coordinate].value!=value:
            raise Problem('FORM_READONLY_CHANGED','固定信息被修改；请只填写黄色单元格。',422)
    for ws in wb:
        if ws.title=='_context':
            continue
        if ws.title not in context['shapes']:
            raise Problem('FORM_STRUCTURE_CHANGED','确认表包含额外工作表。',422)
        for row in ws:
            if any(cell.data_type=='f' for cell in row):
                raise Problem('FORM_FORMULA_NOT_ALLOWED','确认表请填写实际数值，不使用公式。',422)
    return wb


def issue_form(store, project, path):
    wb=Workbook();wb.remove(wb.active)
    rows=[]
    items={x['row_id']:x for x in project['facts'].get('items',[])}
    for issue in project['issues']:
        rows.append([issue['id'],issue['row_id'],issue['message'],issue['field'],issue['answer_type'],
                     cell_value(issue.get('evidence')),cell_value(issue.get('options')),'','','',
                     {k:items.get(issue['row_id'],{}).get(k) for k in ('shown_model','model_family','requirements','quantity_per_cabinet','unit','supply_scope')}])
    ws=add_sheet(wb,'问题确认',['问题ID','柜体/元件ID','待确认问题','关联字段','填写类型','来源证据','可选信息','答复','工程师确认(是)','关联元件修订JSON(选填)','当前元件信息'],rows,[20,18,55,24,22,45,40,55,20,70,75])
    add_sheet(wb,'填写说明',['项目ID','项目版本','确认人','说明'],[[project['id'],project['revision'],'',
        '黄色格填写；答复后填写是。数量填数字，供货范围填英文代码；engineering_note需写具体结论；material_code填正式编码或NON_CATALOG。缺图问题需重新上传。']], [35,14,24,100])
    editable={'填写说明!C2'}
    for i in range(2,len(rows)+2):
        editable|={f'问题确认!H{i}',f'问题确认!I{i}',f'问题确认!J{i}'}
    return sign_book(wb,store,project,'issues',editable,path)


def engineering_template(facts):
    def placements(cab,schematic=False):
        result=[]
        for x in facts['items']:
            if x['cabinet_id']!=cab['cabinet_id']:continue
            q=number(x['quantity_per_cabinet'],positive=True)
            # Linear/weight consumables require explicit engineer exclusion or geometry.
            count=int(q) if q==q.to_integral_value() and q<=100 else 0
            for instance in range(1,count+1):
                row={'row_id':x['row_id'],'instance':instance,'x_mm':None,'y_mm':None}
                if not schematic:row.update(width_mm=None,height_mm=None)
                result.append(row)
        return result
    return {'drawing_title':'元件布置与接线关系图','reviewed':False,'reviewer':'','revision':'A',
        'cabinets':[{'cabinet_id':cab['cabinet_id'],'width_mm':cab.get('width_mm'),
            'height_mm':cab.get('height_mm'),'depth_mm':cab.get('depth_mm'),
            'layout':placements(cab),'schematic':placements(cab,True),
            'wires':[dict(edge,from_terminal='',to_terminal='',conductor_spec='',wire_label='')
                for edge in facts.get('connections',[]) if any(x['row_id']==edge['from'] and x['cabinet_id']==cab['cabinet_id'] for x in facts['items'])],
            'external_nodes':[],'wiring_reviewed':False,'non_layout_row_ids':[],'non_layout_reason':'','non_wired_row_ids':[]}
            for cab in facts['cabinets']]}


def bom_form(store, project, path):
    wb=Workbook();wb.remove(wb.active)
    editable=set()
    headers=['元件ID','设备/柜体编号','回路/位号','元器件名称','关键技术参数','推荐型号','物料编码','厂商名称',
        '单柜数量','柜体数量','总数量','单位','供货范围','正式编码(需改时填)','确认单柜数量(需改时填)','工程师确认(是)','修改意见']
    rows=[]
    for row in project['bom']:
        rows.append([row['row_id'],row['cabinet_id'],row['device_tag'],row['role'],row['requirements'],row['description'],row['material_code'],row['manufacturer'],
            row['quantity_per_cabinet'],row['cabinet_quantity'],row['quantity'],row['unit'],row['supply_scope'],'','','',''])
    add_sheet(wb,'BOM确认',headers,rows,[16,22,18,20,48,46,24,20,14,14,14,12,24,26,24,22,40])
    for i in range(2,len(rows)+2):
        editable|={f'BOM确认!{col}{i}' for col in 'NOPQ'}
    revisions=[[r['row_id'],r['cabinet_id'],r['unit'],r['supply_scope'],'','','','',''] for r in project['bom']]
    add_sheet(wb,'其他元件修订',['元件ID','原柜号','原单位','原供货范围','新柜号(需改时填)','新单位(需改时填)','新供货范围(英文代码)','新型号(需改时填)','完整新requirements JSON(需改时填)'],revisions,
              [18,24,16,26,26,26,35,40,80])
    for i in range(2,len(revisions)+2):editable|={f'其他元件修订!{col}{i}' for col in 'EFGHI'}
    cabinet_rows=[[c['cabinet_id'],c.get('quantity'),c.get('width_mm'),c.get('height_mm'),c.get('depth_mm'),'','','',''] for c in project['facts']['cabinets']]
    add_sheet(wb,'柜体修订',['柜号','原柜体数量','原宽mm','原高mm','原深mm','新柜体数量(需改时填)','新宽mm(需改时填)','新高mm(需改时填)','新深mm(需改时填)'],cabinet_rows)
    for i in range(2,len(cabinet_rows)+2):editable|={f'柜体修订!{col}{i}' for col in 'FGHI'}
    prices=[]
    for row in project['bom']:
        if row['supply_scope']=='OUR_PURCHASE':
            p=row['price']
            prices.append([row['row_id'],row['material_code'],row['unit'],canonical(p.get('references',[])),
                p.get('unit_price_ex_tax'),p.get('tax_rate'),p.get('basis',''),'是' if p.get('confirmed') else ''])
    add_sheet(wb,'价格确认',['元件ID','物料编码','计价单位','参考价格(不作正式报价)','确认未税采购单价','税率(如0.13)','价格依据/单据编号','价格确认(是)'],prices,[18,24,14,60,24,22,45,24])
    for i in range(2,len(prices)+2):
        editable|={f'价格确认!{col}{i}' for col in 'EFGH'}
    add_sheet(wb,'报价设置',['确认人','材料加成率','折扣率','报价设置确认(是)','其他费用未税金额','其他费用税率','其他费用说明/依据','其他费用确认(是)'],
        [['',store.settings.markup,store.settings.discount,'',0,0,'','']],[26,22,22,26,26,24,55,26])
    editable|={f'报价设置!{col}2' for col in 'ABCDEFGH'}
    payload=project.get('engineering') or engineering_template(project['facts'])
    add_sheet(wb,'CAD工程参数',['说明','工程参数JSON'],[['按实际尺寸和接线填写，reviewed与wiring_reviewed为true且填写reviewer。坐标为毫米，左下为原点。外部节点ID必须EXT_开头。未填齐可以先确认BOM，CAD将提示待补充。',canonical(payload)]],[70,150])
    editable.add('CAD工程参数!B2')
    if len(canonical(payload))>30000:
        raise Problem('ENGINEERING_FORM_TOO_LARGE','工程参数超过Excel单格容量，请拆分项目。',422)
    return sign_book(wb,store,project,'bom',editable,path)


def read_issues(wb):
    approver=str(wb['填写说明']['C2'].value or '').strip()
    if not approver:
        raise Problem('APPROVER_REQUIRED','请在填写说明中填写确认人。',422)
    answers={}
    for row in wb['问题确认'].iter_rows(min_row=2,values_only=True):
        answers[row[0]]={'answer':row[7],'confirmed':row[8],'corrections':row[9]}
    return approver,answers


def read_bom(wb, project):
    approver=str(wb['报价设置']['A2'].value or '').strip()
    if not approver:
        raise Problem('APPROVER_REQUIRED','请在报价设置中填写工程确认人。',422)
    edits={}
    for r in wb['BOM确认'].iter_rows(min_row=2,values_only=True):
        if str(r[15] or '').strip() not in YES:
            raise Problem('BOM_NOT_ALL_CONFIRMED','每条BOM都需要工程师填写“是”，包括甲供和预留项。',422)
        edits[r[0]]={'code':str(r[13] or '').strip(),'quantity':r[14],'note':str(r[16] or '').strip()}
    for r in wb['其他元件修订'].iter_rows(min_row=2,values_only=True):
        change={}
        for field,value in zip(('cabinet_id','unit','supply_scope','shown_model','requirements'),r[4:9]):
            if value in (None,''):continue
            if field=='requirements':
                try:value=json.loads(str(value))
                except ValueError:raise Problem('REQUIREMENTS_INVALID','参数修订需填写完整JSON对象。',422) from None
                if not isinstance(value,dict):raise Problem('REQUIREMENTS_INVALID','参数修订必须为JSON对象。',422)
            else:value=str(value).strip()
            change[field]=value
        edits[r[0]]['fields']=change
    cabinet_edits={}
    for r in wb['柜体修订'].iter_rows(min_row=2,values_only=True):
        cabinet_edits[r[0]]={k:v for k,v in zip(('quantity','width_mm','height_mm','depth_mm'),r[5:9]) if v not in (None,'')}
    prices={}
    for r in wb['价格确认'].iter_rows(min_row=2,values_only=True):
        prices[r[0]]={'unit_price_ex_tax':r[4],'tax_rate':r[5],'basis':str(r[6] or '').strip(),
            'confirmed':str(r[7] or '').strip() in YES,'reference_only':False}
    settings=dict(zip(('approver','markup','discount','confirmed','fees','fees_tax','fees_basis','fees_confirmed'),
                      [wb['报价设置'].cell(2,i).value for i in range(1,9)]))
    settings['confirmed']=str(settings['confirmed'] or '').strip() in YES
    settings['fees_confirmed']=str(settings['fees_confirmed'] or '').strip() in YES
    try:
        engineering=json.loads(str(wb['CAD工程参数']['B2'].value or '{}'))
        if not isinstance(engineering,dict):raise ValueError()
    except ValueError:
        raise Problem('ENGINEERING_JSON_INVALID','CAD工程参数必须为有效JSON对象，未准备好可保留原值。',422) from None
    return approver,edits,prices,settings,engineering,cabinet_edits
