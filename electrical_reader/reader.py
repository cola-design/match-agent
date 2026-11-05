"""Native drawing parsing and evidence-preserving rendering; no database access."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

import fitz
from domain import decode, normalized


def dump(path, value):
    path = Path(path);tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8');tmp.replace(path)


def safe_name(value):return Path(str(value).replace('\\','/')).name[:180] or 'drawing'


def native_texts(page, source_id):
    rows=[]
    for block in page.get_text('dict')['blocks']:
        if block.get('type') != 0:continue
        for line in block['lines']:
            for span in line['spans']:
                text=span['text'].strip()
                if not text:continue
                rect=fitz.Rect(span['bbox'])*page.rotation_matrix
                rows.append({'anchor_id':f'{source_id}:p{page.number+1}:t{len(rows)+1}', 'raw':text,
                             'bbox_page':[round(x,4) for x in rect], 'method':'pdf_native_text', 'decoded':decode(text)})
    return rows


def normal_rect(rect, page):
    return [round(rect.x0/page.width,6),round(rect.y0/page.height,6),round(rect.x1/page.width,6),round(rect.y1/page.height,6)]


def meaningful_bounds(page, texts):
    boxes=[fitz.Rect(row['bbox_page']) for row in texts]
    for path in page.get_drawings():
        rect=path['rect']*page.rotation_matrix
        if rect.is_empty or not rect.is_valid or rect.get_area()>page.rect.get_area()*.92:continue
        boxes.append(rect)
    if not boxes:return page.rect
    bounds=fitz.Rect(boxes[0])
    for rect in boxes[1:]:bounds|=rect
    if bounds.is_empty:return page.rect
    return fitz.Rect(bounds.x0-12,bounds.y0-12,bounds.x1+12,bounds.y1+12)&page.rect


def make_units(page, texts, source_id, bounds, dpi=300):
    result=[]
    def add(rect,kind,depth=0):
        index=len(result)+1
        covered=[]
        for row in texts:
            r=fitz.Rect(row['bbox_page'])
            if rect.contains(r.tl) or rect.contains((r.tl+r.br)/2):covered.append(row)
        text_bytes=len(json.dumps([{'id':a['anchor_id'],'raw':a['raw']} for a in covered],ensure_ascii=False).encode())
        if kind=='detail' and (len(covered)>80 or text_bytes>27000):
            if depth>=8 or min(rect.width,rect.height)<3:raise ValueError('局部文字密度过高，无法在预算内完整提取；请拆分图面或核对异常文字对象')
            if rect.width>=rect.height:
                mid=(rect.x0+rect.x1)/2;pad=rect.width*.05
                add(fitz.Rect(rect.x0,rect.y0,mid+pad,rect.y1),kind,depth+1)
                add(fitz.Rect(mid-pad,rect.y0,rect.x1,rect.y1),kind,depth+1)
            else:
                mid=(rect.y0+rect.y1)/2;pad=rect.height*.05
                add(fitz.Rect(rect.x0,rect.y0,rect.x1,mid+pad),kind,depth+1)
                add(fitz.Rect(rect.x0,mid-pad,rect.x1,rect.y1),kind,depth+1)
            return
        result.append({'unit_id':f'{source_id}-p{page.number+1}-u{index}', 'source_id':source_id,
                       'page':page.number+1,'kind':kind,'bbox_page':list(rect),'bbox_normalized':normal_rect(rect,page.rect),
                       'page_size':[page.rect.width,page.rect.height], 'anchors':covered,'dpi':dpi})
    add(bounds,'overview')
    span=1500*72/dpi;stride=span*.82
    nx=max(1,math.ceil(max(0,bounds.width-span)/stride)+1)
    ny=max(1,math.ceil(max(0,bounds.height-span)/stride)+1)
    if nx*ny>400:raise ValueError('单页分区超过400个，请拆分超大画布；未截断处理')
    for iy in range(ny):
        for ix in range(nx):
            x=bounds.x0+ix*stride;y=bounds.y0+iy*stride
            rect=fitz.Rect(x,y,min(bounds.x1,x+span),min(bounds.y1,y+span))
            add(rect,'detail')
    return result


def convert_dwg(path, target):
    binary=os.getenv('DWGREAD_BIN') or shutil.which('dwgread')
    if not binary:raise ValueError('缺少DWG读取器：配置LibreDWG的DWGREAD_BIN；未将DWG当PDF处理')
    done=subprocess.run([binary,'-O','DXF','-o',str(target),str(path)],capture_output=True,timeout=180)
    if done.returncode or not target.exists() or target.stat().st_size<100:
        raise ValueError('DWG解码未成功；保留源文件，需核对DWG版本、外部参照或换用经验证的转换器')
    return target


def cad_to_pdf(path, out):
    import ezdxf
    from ezdxf import bbox, disassemble
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf, config
    from ezdxf.fonts import fonts
    doc=ezdxf.readfile(path)
    if doc.acad_release not in ('R12','R2000','R2004','R2007','R2010','R2013','R2018'):
        pass # ezdxf remains the authority on whether the file can be decoded.
    combined=fitz.open();all_rows=[];warnings=[]
    cjk=os.getenv('CJK_FONT_FILE','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if Path(cjk).is_file():
        fonts.font_manager.scan_folder(Path(cjk).parent)
        for style in doc.styles:style.dxf.font=Path(cjk).name
        warnings.append('CAD预览使用可显示中文的替代字体；原生文字原文仍保留，预览不作为原设计图交付')
    else:warnings.append('未配置CJK_FONT_FILE，CAD中文预览可能缺字；应检查原生文字与预览')
    for lay in doc.layouts:
        if len(lay)==0:continue
        if not any(e.dxftype() not in ('VIEWPORT',) for e in lay):continue
        backend=pymupdf.PyMuPdfBackend()
        cfg=config.Configuration(background_policy=config.BackgroundPolicy.WHITE, color_policy=config.ColorPolicy.BLACK)
        context=RenderContext(doc)
        Frontend(context,backend,config=cfg).draw_layout(lay,finalize=True)
        box=backend.player().bbox()
        if not box.has_data:continue
        page_spec=layout.Page(420,297,layout.Units.mm,margins=layout.Margins.all(8))
        settings=layout.Settings()
        placement=layout.Layout(box,flip_y=backend._init_flip_y)
        final_page=placement.get_final_page(page_spec,settings)
        settings2=copy.copy(settings)
        settings2.output_coordinate_space=pymupdf.get_coordinate_output_space(final_page)
        matrix=placement.get_placement_matrix(final_page,settings=settings2,top_origin=True)
        cad_rows=[]
        for index,ent in enumerate(disassemble.recursive_decompose(lay)):
            typ=ent.dxftype()
            if typ not in ('TEXT','MTEXT','ATTRIB','ATTDEF'):continue
            layer=doc.layers.get(ent.dxf.layer)
            if layer.is_off() or layer.is_frozen():continue
            text=ent.plain_text() if hasattr(ent,'plain_text') else ent.dxf.get('text','')
            if not text.strip():continue
            ebox=bbox.extents([ent],fast=False)
            if not ebox.has_data:continue
            points=[matrix.transform(p) for p in ebox.rect_vertices()]
            rect=[min(p.x for p in points),min(p.y for p in points),max(p.x for p in points),max(p.y for p in points)]
            cad_rows.append({'raw':text,'bbox_page':rect,'method':'cad_native_text','cad_layer':ent.dxf.layer,
                             'cad_layout':lay.name,'cad_handle':ent.dxf.get('handle',''),
                             'cad_insert_wcs':list(ent.dxf.insert),'decoded':decode(text)})
        rendered=fitz.open(stream=backend.get_pdf_bytes(page_spec,settings=settings),filetype='pdf')
        combined.insert_pdf(rendered);all_rows.append(cad_rows)
    if not combined.page_count:raise ValueError('CAD没有可渲染的图面；未输出空识别结果')
    combined.save(out);combined.close()
    missing=[b.name for b in doc.blocks if b.block.dxf.flags & 4]
    if missing:warnings.append('存在外部参照图块；需要核对关联文件是否齐全，未保证外部图纸内容完整')
    if len(all_rows)>1:warnings.append('同一CAD含多个布局，布局之间可能重复显示同一设备；当前提取保留来源，不直接累计成采购数量')
    return all_rows,warnings


def parse_source(path, folder, source_id=None):
    path=Path(path);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    sid=source_id or uuid.uuid4().hex
    suffix=path.suffix.lower();warnings=[];cad_rows=None
    pdf=path
    if suffix=='.dwg':
        converted=convert_dwg(path,folder/'decoded.dxf')
        pdf=folder/'rendered.pdf';cad_rows,warnings=cad_to_pdf(converted,pdf)
    elif suffix=='.dxf':
        pdf=folder/'rendered.pdf';cad_rows,warnings=cad_to_pdf(path,pdf)
    elif suffix!='.pdf':raise ValueError('仅支持PDF、DWG和DXF')
    doc=fitz.open(pdf)
    if doc.needs_pass:raise ValueError('PDF已加密，需提供可读取版本')
    if not 0<doc.page_count<=200:raise ValueError('单文件支持1至200页；请分批上传，不截取前200页冒充完整结果')
    pages=[];units=[]
    for page in doc:
        texts=native_texts(page,sid)
        if cad_rows is not None:
            texts=[]
            for row in cad_rows[page.number]:
                row=dict(row,anchor_id=f'{sid}:p{page.number+1}:t{len(texts)+1}')
                texts.append(row)
        bounds=meaningful_bounds(page,texts)
        native_cad=bool(cad_rows is not None)
        pages.append({'page':page.number+1,'size':list(page.rect),'rotation':page.rotation,'anchors':texts,
                      'needs_visual':True,'native_text_available':bool(texts),'native_cad':native_cad})
        units.extend(make_units(page,texts,sid,bounds))
    result={'source_id':sid,'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'input_format':suffix[1:],'rendered_pdf':str(pdf.resolve()),'pages':pages,'units':units,'warnings':warnings,
            'scope':'原生文字与图形已解析；器件符号、标注归属和数量仍需视觉识别及工程核对'}
    dump(folder/'source.json',result)
    return result


def render(source, unit_id):
    unit=next(u for u in source['units'] if u['unit_id']==unit_id)
    doc=fitz.open(source['rendered_pdf']);page=doc[unit['page']-1];rect=fitz.Rect(unit['bbox_page'])
    scale=unit['dpi']/72
    if unit['kind']=='overview':scale=min(scale,1600/max(rect.width,rect.height))
    return page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False).tobytes('png')


def model_context(source, unit):
    rect=fitz.Rect(unit['bbox_page']);anchors=[]
    for a in unit['anchors']:
        box=fitz.Rect(a['bbox_page'])&rect
        if box.is_empty:continue
        anchors.append({'anchor_id':a['anchor_id'],'raw':a['raw'],
                        'bbox_unit':[(box.x0-rect.x0)/rect.width,(box.y0-rect.y0)/rect.height,
                                     (box.x1-rect.x0)/rect.width,(box.y1-rect.y0)/rect.height]})
    context={'unit_id':unit['unit_id'],'source_id':source['source_id'],'filename':source['filename'],
             'page':unit['page'],'kind':unit['kind'],'anchors':anchors,
             'scope':'当前分区；bbox以当前图片归一化，跨区看不到的内容不推断'}
    if unit['kind']=='overview':
        # Overview indexes sheets/cabinets; detailed text is read in detail units.
        # The complete native text remains in source.json and exported reports.
        context['native_anchor_total']=len(anchors)
        context['anchors']=anchors[:60]
        context['overview_text_index_is_subset']=len(anchors)>60
        context['scope']+='；概览只提供最多60条文字索引，完整原文由细分区处理并全部保留在下载文件中'
    size=len(json.dumps(context,ensure_ascii=False).encode())
    if size>44000:raise ValueError('该分区文字超过模型输入预算，需要进一步拆分；未截断原文')
    return context
