"""Stdlib-only XLSX geometry, embedded in Dify code nodes. No openpyxl in the sandbox."""
import base64 as _lx_b64
import io as _lx_io
import json as _lx_json
import math as _lx_math
import re as _lx_re
import unicodedata as _lx_unicode
import zipfile as _lx_zip
from xml.etree import ElementTree as _lx_et

_LX_NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_LX_REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_lx_et.register_namespace('',_LX_NS)
_lx_et.register_namespace('r',_LX_REL)


class LayoutCapacityError(ValueError):
    pass


def _lx_tag(name):return '{'+_LX_NS+'}'+name


def _lx_insert_before(parent,element,following):
    position=next((i for i,child in enumerate(parent) if child.tag in {_lx_tag(name) for name in following}),len(parent))
    parent.insert(position,element)


def _lx_col(number):
    result=''
    while number:
        number,remainder=divmod(number-1,26);result=chr(65+remainder)+result
    return result


def _lx_col_number(value):
    result=0
    for char in value:result=result*26+ord(char)-64
    return result


def _lx_lines(value,width):
    if value is None:return 1
    text=str(value).replace('\r\n','\n').replace('\r','\n')
    available=max(5,width-2)
    total=0
    for line in text.split('\n'):
        weight=sum(2.1 if _lx_unicode.east_asian_width(c) in ('W','F') else 1 for c in line)
        total+=max(1,_lx_math.ceil(weight/available))
    return total


def layout_template(template_base64,payload_json,spec):
    """Return a styled EMPTY template; the existing Excel plugin fills unchanged cell values."""
    payload=_lx_json.loads(payload_json)
    cells=payload.get('cells',{})
    if not isinstance(cells,dict):raise ValueError('Excel cells必须是对象')
    buffer=_lx_io.BytesIO()
    with _lx_zip.ZipFile(_lx_io.BytesIO(_lx_b64.b64decode(template_base64))) as original:
        files={name:original.read(name) for name in original.namelist()}
    book=_lx_et.fromstring(files['xl/workbook.xml'])
    rels=_lx_et.fromstring(files['xl/_rels/workbook.xml.rels'])
    targets={r.attrib['Id']:r.attrib['Target'].lstrip('/') for r in rels}
    names=book.find(_lx_tag('definedNames'))
    if names is None:
        names=_lx_et.Element(_lx_tag('definedNames'));_lx_insert_before(book,names,('calcPr','oleSize','customWorkbookViews','pivotCaches','extLst'))
    for item in list(names):
        if item.get('name') in ('_xlnm.Print_Area','_xlnm.Print_Titles'):names.remove(item)
    for index,sheet in enumerate(book.find(_lx_tag('sheets'))):
        name=sheet.get('name');config=spec.get(name)
        if name=='系统数据':
            sheet.set('state','veryHidden');continue
        if not config:continue
        target=targets[sheet.get('{'+_LX_REL+'}id')]
        path=target if target.startswith('xl/') else 'xl/'+target
        root=_lx_et.fromstring(files[path]);data=root.find(_lx_tag('sheetData'))
        start=config['start'];columns=config['columns'];minimum=config.get('minimum',start-1)
        values={}
        metadata=set()
        for key,value in cells.items():
            if '!' not in key:continue
            prefix,coordinate=key.rsplit('!',1)
            if prefix.strip("'")!=name:continue
            match=_lx_re.fullmatch(r'([A-Z]+)(\d+)',coordinate)
            if not match:raise ValueError('Excel单元格地址无效')
            row=int(match.group(2))
            if row>=130 and name in ('BOM确认','资料问题确认'):
                metadata.add(row);continue
            values[coordinate]=value
        rows=[int(_lx_re.search(r'\d+$',key).group()) for key in values]
        last=max([minimum]+rows)
        if config.get('hide_if_empty') and last<start:
            sheet.set('state','hidden')
            continue
        if last-start+1>120 and name not in ('01 报价汇总',):
            raise LayoutCapacityError(name+'超过120行，请分批；没有输出截断表格')
        widths=config['widths']
        merged_widths={}
        merges=root.find(_lx_tag('mergeCells'))
        for merged in merges if merges is not None else []:
            hit=_lx_re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)',merged.get('ref',''))
            if hit and hit.group(2)==hit.group(4):
                first=_lx_col_number(hit.group(1));end=_lx_col_number(hit.group(3))
                merged_widths[hit.group(1)+hit.group(2)]=sum(widths[first-1:end])
        for row in list(data):
            number=int(row.get('r','0'))
            if number>=130 and name in ('BOM确认','资料问题确认'):
                row.set('hidden','1');row.set('ht','1');row.set('customHeight','1');continue
            if number>last:
                data.remove(row);continue
            if number<start:continue
            height=max([config.get('min_height',34)]+[
                9+16*_lx_lines(values.get(_lx_col(col)+str(number)),merged_widths.get(_lx_col(col)+str(number),widths[col-1]))
                for col in range(1,columns+1)])
            if height>409:
                raise LayoutCapacityError(name+'第'+str(number)+'行文字过长，超过Excel单行显示上限；请精简重复说明或拆分需求，未输出会截断的表格')
            row.set('ht',str(height));row.set('customHeight','1')
        dimension=root.find(_lx_tag('dimension'))
        if dimension is not None:dimension.set('ref','A1:'+_lx_col(columns)+str(max(last,max(metadata) if metadata else last)))
        filters=root.find(_lx_tag('autoFilter'))
        if filters is not None:filters.set('ref','A'+str(start-1)+':'+_lx_col(columns)+str(max(start-1,last)))
        properties=root.find(_lx_tag('sheetPr'))
        if properties is None:properties=_lx_et.Element(_lx_tag('sheetPr'));root.insert(0,properties)
        setup_properties=properties.find(_lx_tag('pageSetUpPr'))
        if setup_properties is None:setup_properties=_lx_et.SubElement(properties,_lx_tag('pageSetUpPr'))
        setup_properties.set('fitToPage','1');setup_properties.set('autoPageBreaks','1')
        setup=root.find(_lx_tag('pageSetup'))
        if setup is None:
            setup=_lx_et.Element(_lx_tag('pageSetup'));_lx_insert_before(root,setup,('headerFooter','rowBreaks','colBreaks','drawing','legacyDrawing','tableParts','extLst'))
        setup.attrib.update(orientation=config.get('orientation','landscape'),paperSize=str(config.get('paper',8)),fitToWidth='1',fitToHeight='0')
        margins=root.find(_lx_tag('pageMargins'))
        if margins is None:
            margins=_lx_et.Element(_lx_tag('pageMargins'));_lx_insert_before(root,margins,('pageSetup','headerFooter','rowBreaks','colBreaks','drawing','legacyDrawing','tableParts','extLst'))
        margins.attrib.update(left='0.25',right='0.25',top='0.35',bottom='0.35',header='0.15',footer='0.15')
        escaped="'"+name.replace("'","''")+"'"
        area=_lx_et.SubElement(names,_lx_tag('definedName'),{'name':'_xlnm.Print_Area','localSheetId':str(index)})
        area.text=escaped+'!$A$1:$'+_lx_col(columns)+'$'+str(last)
        repeat=_lx_et.SubElement(names,_lx_tag('definedName'),{'name':'_xlnm.Print_Titles','localSheetId':str(index)})
        repeat.text=escaped+'!$1:$'+str(config.get('header_end',start-1))
        files[path]=_lx_et.tostring(root,encoding='utf-8',xml_declaration=True)
    files['xl/workbook.xml']=_lx_et.tostring(book,encoding='utf-8',xml_declaration=True)
    with _lx_zip.ZipFile(buffer,'w',compression=_lx_zip.ZIP_DEFLATED) as output:
        for name,content in files.items():output.writestr(name,content)
    encoded=_lx_b64.b64encode(buffer.getvalue()).decode('ascii')
    if len(encoded)>55000:raise LayoutCapacityError('动态Excel模板超过传递预算，请分批')
    return encoded
