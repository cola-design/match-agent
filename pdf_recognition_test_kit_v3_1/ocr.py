"""Local OCR fallback for scanned or outline-font PDFs; no external uploads."""
import csv
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import math
from difflib import SequenceMatcher

import fitz

_RAPID_ENGINE=None


def rapidocr(page,sid,dpi=225):
    """Run bundled Chinese/English ONNX OCR on overlapping high-detail tiles."""
    global _RAPID_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("缺少rapidocr-onnxruntime；安装后可在CPU运行中文OCR") from exc
    import numpy as np
    from recognize import decode,norm
    if _RAPID_ENGINE is None:
        _RAPID_ENGINE=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=2)
    rect=ink_bounds(page);scale=dpi/72
    # The overlap retains labels crossing a cut. It does not turn repeated
    # same-model labels at different positions into one physical component.
    span=1600/scale;overlap=span*.12
    nx=max(1,math.ceil(rect.width/span));ny=max(1,math.ceil(rect.height/span))
    if nx*ny>64:
        raise RuntimeError("单页OCR分区超过64个，需要拆分大画布；未跳过后续分区")
    rows=[]
    full_page_used=rect.width*rect.height*scale*scale<=18_000_000
    windows=[(-1,-1,rect)] if full_page_used else []
    for iy in range(ny):
        for ix in range(nx):
            clip=fitz.Rect(max(rect.x0,rect.x0+ix*rect.width/nx-overlap),
                           max(rect.y0,rect.y0+iy*rect.height/ny-overlap),
                           min(rect.x1,rect.x0+(ix+1)*rect.width/nx+overlap),
                           min(rect.y1,rect.y0+(iy+1)*rect.height/ny+overlap))
            if nx*ny>1 or not full_page_used:windows.append((ix,iy,clip))
    for ix,iy,clip in windows:
            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
            data=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)
            detections,_=_RAPID_ENGINE(data)
            # Pixmap origins are rounded device coordinates, not the requested
            # floating-point clip origin. Use the actual origin for grounding.
            for polygon,raw,confidence in detections or []:
                poly=[[(float(x)+pix.x)/scale,(float(y)+pix.y)/scale] for x,y in polygon]
                b=[min(x for x,y in poly),min(y for x,y in poly),max(x for x,y in poly),max(y for x,y in poly)]
                duplicate=None
                for old in rows:
                    inter=fitz.Rect(old["bbox"])&fitz.Rect(b)
                    if inter.get_area()<=min(fitz.Rect(old["bbox"]).get_area(),fitz.Rect(b).get_area())*.6:continue
                    oldtext="".join(norm(old["raw"]).casefold().split());newtext="".join(norm(raw).casefold().split())
                    contained=oldtext in newtext or newtext in oldtext
                    similar=SequenceMatcher(None,oldtext,newtext).ratio()>=.65
                    if contained or similar:
                        duplicate=old;break
                if duplicate is not None:
                    duplicate.setdefault("also_seen_in_tiles",[]).append([ix,iy])
                    if norm(duplicate["raw"])!=norm(raw):
                        full_new=len(raw)>len(duplicate["raw"])*1.15 and confidence>=duplicate["ocr_confidence"]-.15
                        better=confidence>duplicate["ocr_confidence"]+.01 and len(raw)>=len(duplicate["raw"])*.8
                        alternate={"raw":raw,"bbox":b,"confidence":float(confidence),"tile":[ix,iy]}
                        if full_new or better:
                            alternate={"raw":duplicate["raw"],"bbox":duplicate["bbox"],"confidence":duplicate["ocr_confidence"],"tile":duplicate["tile"]}
                            duplicate.update(raw=raw,bbox=[round(v,4) for v in b],polygon=poly,
                                             ocr_confidence=round(float(confidence),4),tile=[ix,iy],**decode(raw))
                        duplicate.setdefault("alternative_ocr_readings",[]).append(alternate)
                    continue
                rows.append({"id":f"{sid}:p{page.number+1}:ocr{len(rows)+1}","raw":raw,
                             "bbox":[round(v,4) for v in b],"polygon":poly,
                             "font_size":max(1,min(b[2]-b[0],b[3]-b[1])),"direction":[1,0],
                             "source":"local_rapidocr_onnx","ocr_confidence":round(float(confidence),4),
                             "tile":[ix,iy],"transcription_verified":False,**decode(raw)})
    return rows,{"engine":"rapidocr_onnxruntime","dpi":dpi,"tiles":len(windows),"full_page_pass_used":full_page_used,"bbox":list(rect),
                 "lines":len(rows),"external_api_calls":0,"hardware":"CPU"}


def ink_bounds(page):
    boxes=[]
    for p in page.get_drawings():
        r=p["rect"]*page.rotation_matrix
        if r.is_valid and not r.is_empty:
            boxes.append(r)
    for b in page.get_text("blocks"):
        boxes.append(fitz.Rect(b[:4])*page.rotation_matrix)
    for image in page.get_image_info():
        boxes.append(fitz.Rect(image["bbox"])*page.rotation_matrix)
    if not boxes:
        return page.rect
    r=fitz.Rect(boxes[0])
    for b in boxes[1:]:
        r|=b
    return (r+(-8,-8,8,8))&page.rect


def needs_ocr(annotations,path_count,image_area_ratio=0):
    useful=sum(a["kind"] not in ("other","calculation") for a in annotations)
    return not annotations or (len(annotations)<25 and useful<3 and path_count>200) or (len(annotations)<40 and image_area_ratio>.25)


def tesseract(page,sid,tessdata=None,dpi=400):
    executable=shutil.which("tesseract")
    if not executable:
        raise RuntimeError("缺少本地Tesseract；该图需要OCR，原生文字不足不能宣称读全")
    rect=ink_bounds(page)
    scale=dpi/72
    if rect.width*rect.height*scale*scale>50_000_000:
        raise RuntimeError("该页超过本地OCR像素预算，需要分区识别；未降低分辨率冒充完整")
    # A caller-controlled model directory is passed only as data, never a shell
    # command. TLS/network/model downloads are outside this inference function.
    languages="eng"
    if tessdata and (Path(tessdata)/"chi_sim.traineddata").is_file():
        languages="chi_sim+eng"
    with tempfile.TemporaryDirectory(prefix="electrical-ocr-") as temp:
        png=Path(temp)/"page.png"
        page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False).save(png)
        args=[executable,str(png),"stdout","-l",languages,"--psm","11","--dpi",str(dpi)]
        if tessdata:
            args.extend(["--tessdata-dir",str(Path(tessdata).resolve())])
        args.extend(["-c","tessedit_create_tsv=1"])
        env=dict(os.environ);env["OMP_THREAD_LIMIT"]="2"
        try:done=subprocess.run(args,capture_output=True,text=True,encoding="utf-8",timeout=120,env=env)
        except subprocess.TimeoutExpired as exc:raise RuntimeError("本地OCR超时；未以空白结果替代") from exc
        if done.returncode:
            raise RuntimeError("本地OCR执行失败；检查OCR语言包和输入文件，未用空白结果替代")
        if not done.stdout.startswith("level\tpage_num\t"):
            raise RuntimeError("OCR未返回预期TSV结构；未将不可解析输出当作空白成功")
    lines={}
    for row in csv.DictReader(io.StringIO(done.stdout),delimiter="\t"):
        text=(row.get("text") or "").strip()
        if not text or row.get("level")!="5":
            continue
        key=tuple(row[x] for x in ("block_num","par_num","line_num"))
        x,y,w,h=[int(row[k]) for k in ("left","top","width","height")]
        lines.setdefault(key,[]).append({"raw":text,"bbox":[x/scale+rect.x0,y/scale+rect.y0,
                                      (x+w)/scale+rect.x0,(y+h)/scale+rect.y0],
                                      "confidence":max(0,float(row["conf"]))/100})
    from recognize import decode
    output=[]
    for tokens in lines.values():
        tokens.sort(key=lambda t:t["bbox"][0])
        # Preserve every token. Joining words is only a line transcript; its box
        # is the measured union, not fabricated character-level positions.
        b=[min(t["bbox"][0] for t in tokens),min(t["bbox"][1] for t in tokens),
           max(t["bbox"][2] for t in tokens),max(t["bbox"][3] for t in tokens)]
        raw=" ".join(t["raw"] for t in tokens)
        output.append({"id":f"{sid}:p{page.number+1}:ocr{len(output)+1}","raw":raw,
                       "bbox":[round(v,4) for v in b],"font_size":max(1,b[3]-b[1]),"direction":[1,0],
                       "source":"local_tesseract_ocr","ocr_confidence":round(min(t["confidence"] for t in tokens),4),
                       "ocr_tokens":tokens,"transcription_verified":False,**decode(raw)})
    return output,{"engine":"tesseract","languages":languages,"dpi":dpi,"bbox":list(rect),
                   "lines":len(output),"external_api_calls":0}
