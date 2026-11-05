"""Evidence-first PDF electrical drawing extraction.

Native annotations and verified wire-row associations are separate from model
interpretations and physical purchase quantities. No database or CAD writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import unicodedata

import fitz

VERSION = "0.2.0"


def norm(s):
    return re.sub(r"[‐‑‒–—−]", "-", unicodedata.normalize("NFKC", str(s))).strip()


def box(values):
    return [round(float(x), 4) for x in values]


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def decode(raw):
    """Decode explicit literal markings; never infer catalogue properties."""
    s = norm(raw).upper()
    p = {}
    kind = "other"
    meaning = ""
    m = re.search(r"(?<![A-Z0-9])(MCCB|MCB|RCBO|RCCB|RCB|RCD)(?=[\d /-]|$)", s)
    if m:
        kind = "device_model"
        p["drawing_abbreviation"] = m[1]
        meaning = {"MCCB": "塑壳断路器", "MCB": "小型断路器", "RCB": "剩余电流保护器件，订货类别待核对",
                   "RCBO": "带过电流保护的剩余电流动作断路器", "RCCB": "不带过电流保护的剩余电流动作断路器",
                   "RCD": "剩余电流保护器件统称"}[m[1]]
        curve = re.search(r"(?:^|[/\s-])([BCD])\s*(\d+(?:\.\d+)?)\s*A(?=$|[/\s(])", s)
        if curve:
            p.update(trip_curve=curve[1], rated_current_a=float(curve[2]))
        trip = re.search(r"MA\d+(?:\.\d+)?A", s)
        if trip:
            p["trip_marking_raw"] = trip[0]
            p["rated_current_a"] = float(re.search(r"\d+(?:\.\d+)?", trip[0])[0])
    elif re.search(r"(?<![A-Z])(?:WDZ[A-Z0-9]*-|NHBV-|ZNBV-|BVR?-|UTP/|CAT\d)", s):
        kind = "cable"
        meaning = "电线、线缆或通信线标注"
        if "WDZB-" in s:
            p.update(halogen_free=True, low_smoke=True, flame_retardant_category="B")
        family = re.search(r"(?:WDZ[A-Z0-9]*-)(BYJ|YJY|YJV|RYJS|RYJSP|RYJ)(?:-|$)", s)
        if family:
            p["family"] = family[1]
            if family[1] == "BYJ":
                p["family_meaning"] = "交联聚烯烃绝缘布电线"
            elif family[1] == "YJY":
                p["family_meaning"] = "交联聚乙烯绝缘、聚烯烃护套电力电缆"
        spec = re.search(r"\d+\s*[X*]\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+\s*[X*]\s*\d+(?:\.\d+)?)*", s)
        if spec:
            p["conductor_groups"] = [{"count": int(n), "area_mm2": float(a)} for n, a in
                                     re.findall(r"(\d+)\s*[X*]\s*(\d+(?:\.\d+)?)", spec[0])]
        routes = {"JDG": "套接紧定式钢导管", "SC": "钢管敷设标识", "CT": "电缆桥架敷设标识"}
        p["containment"] = [{"code": a + b, "meaning": routes[a]} for a, b in
                            re.findall(r"(?<![A-Z])(JDG|SC|CT)(\d*)", s)]
        cat = re.search(r"CAT(\dE?)", s)
        if cat:
            p["category_raw"] = "CAT" + cat[1]
    elif re.fullmatch(r"\d{1,3}\s*[#号]", s):
        kind = "circuit_tag"
    elif re.fullmatch(r"L[123](?:[~～-]L?[123])?(?:[,、]L[123])*|[ABC]相|N|PE|PEN", s):
        kind = "phase"
    elif re.fullmatch(r"\d+(?:\.\d+)?\s*MA", s):
        kind = "residual_current"
    elif re.fullmatch(r"(?:AC|DC)?\s*\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*V", s):
        kind = "voltage_ratio"
        p["voltage_pair_v"] = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    elif re.fullmatch(r"\d+(?:\.\d+)?\s*VA", s):
        kind = "apparent_power"
        p["apparent_power_va"] = float(re.search(r"\d+(?:\.\d+)?", s)[0])
    elif re.fullmatch(r"(?:DC|AC)\s*\d+(?:\.\d+)?\s*V(?:\s+\d+(?:\.\d+)?\s*A)?", s):
        kind = "power_marking"
        v = re.search(r"(\d+(?:\.\d+)?)\s*V", s)
        a = re.search(r"(\d+(?:\.\d+)?)\s*A", s)
        p.update(power_type=s[:2], voltage_v=float(v[1]))
        if a:
            p["current_a"] = float(a[1])
    elif re.match(r"^ATS[\s-]", s):
        kind = "device_model"
        meaning = "自动转换开关型号标注"
        p["drawing_abbreviation"] = "ATS"
    elif re.fullmatch(r"(?:K[AMT]|Q[FS]|FU|SPD|TA|PM)\d*", s):
        kind = "device_tag"
    elif "时间控制器" in s or "时控开关" in s:
        kind = "function_label"
    elif re.match(r"^(?:PE|KX|COS|IJ)\s*[=＝]", s):
        kind = "calculation"
    elif re.fullmatch(r"[A-Z][A-Z0-9]{1,14}-[A-Z0-9][A-Z0-9./+() -]{1,70}", s) and re.search(r"\d", s):
        kind = "unclassified_model"

    if kind in ("device_model", "unclassified_model"):
        poles = re.search(r"(?<!\d)([1-4]P(?:\+N)?)(?![A-Z0-9])", s)
        if poles:
            p["pole_marking"] = poles[1]
        if "rated_current_a" not in p:
            pattern=(r"(?:^|\s)(\d+(?:\.\d+)?)\s*A(?=$|[/\s(-])" if kind=="unclassified_model" else
                     r"(?<![A-Z0-9.])(\d+(?:\.\d+)?)\s*A(?=$|[/\s(-])")
            currents = re.findall(pattern, s)
            if len(currents) == 1:
                p["rated_current_a"] = float(currents[0])
    if kind in ("device_model", "residual_current"):
        leak = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*MA(?=$|[/\s(])", s)
        if leak:
            p["residual_current_ma"] = float(leak[1])
    return {"kind": kind, "meaning": meaning, "explicit_parameters": p}


def native_annotations(page, sid):
    rows = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                raw = span["text"].strip()
                if not raw:
                    continue
                r = fitz.Rect(span["bbox"]) * page.rotation_matrix
                direction = fitz.Point(line["dir"]) * fitz.Matrix(page.rotation_matrix.a, page.rotation_matrix.b,
                                                                  page.rotation_matrix.c, page.rotation_matrix.d, 0, 0)
                rows.append({"id": f"{sid}:p{page.number+1}:t{len(rows)+1}", "raw": raw,
                             "bbox": box(r), "font_size": round(float(span["size"]), 4),
                             "direction": box(direction), "source": "native_pdf_text", **decode(raw)})
    return rows


def segments(page, annotations):
    """Recover wire-sized axis segments from strokes and thin filled strips.

    Native text boxes mask SHX glyph outlines, so those are not treated as wires.
    Thin filled polygons are collapsed to one centreline, not counted twice.
    """
    masked = [fitz.Rect(a["bbox"]) for a in annotations]
    native = [];tiny=[]
    sizes = [a["font_size"] for a in annotations if a["font_size"] > 0]
    scale = median(sizes) if sizes else 6
    min_len = max(2, scale * .45)

    def add(p, q, pid, strip=False):
        p = p * page.rotation_matrix
        q = q * page.rotation_matrix
        dx, dy = abs(p.x-q.x), abs(p.y-q.y)
        if max(dx, dy) < .12:
            return
        mid = (p+q)/2
        if any(r.contains(mid) for r in masked):
            return
        axis = ("h" if dx >= dy else "v") if min(dx,dy)<=max(.15,min_len*.06) else "o"
        value, lo, hi = ((p.y+q.y)/2, min(p.x,q.x), max(p.x,q.x)) if axis == "h" else ((p.x+q.x)/2, min(p.y,q.y), max(p.y,q.y))
        data={"axis": axis, "value": float(value), "lo": lo, "hi": hi, "paths": [pid], "filled_strip": strip,
              "p1":box(p),"p2":box(q)}
        if max(dx,dy)<min_len:
            if axis in ("h","v"):
                tiny.append(data)
        else:
            native.append(data)

    for pid, path in enumerate(page.get_drawings()):
        rect = path["rect"]
        width, height = rect.width, rect.height
        if path.get("fill") is not None and min(width,height) <= max(.8, scale*.2) and max(width,height) >= min_len:
            if width >= height:
                add(fitz.Point(rect.x0,(rect.y0+rect.y1)/2), fitz.Point(rect.x1,(rect.y0+rect.y1)/2),pid,True)
            else:
                add(fitz.Point((rect.x0+rect.x1)/2,rect.y0), fitz.Point((rect.x0+rect.x1)/2,rect.y1),pid,True)
            continue
        for item in path["items"]:
            if item[0] == "l":
                add(item[1],item[2],pid)
            elif item[0] == "re":
                r = item[1]
                for p,q in ((r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)):
                    add(p,q,pid)
            elif item[0] == "qu":
                q=item[1];pts=[q.ul,q.ur,q.lr,q.ll]
                edges=[(pts[i],pts[(i+1)%4]) for i in range(4)]
                lengths=[abs(b-a) for a,b in edges]
                shortest=min(range(4),key=lambda i:lengths[i])
                opposite=(shortest+2)%4
                if lengths[shortest]>0 and max(lengths)/lengths[shortest]>5 and max(lengths)>min_len:
                    add(sum(edges[shortest],fitz.Point(0,0))/2,sum(edges[opposite],fitz.Point(0,0))/2,pid,True)
                else:
                    for a,b in edges:
                        add(a,b,pid)

    buckets={}
    for s in tiny:
        buckets.setdefault((s["axis"],round(s["value"]*4)),[]).append(s)
    for values in buckets.values():
        chain=[]
        def emit(parts):
            if len(parts)<4 or parts[-1]["hi"]-parts[0]["lo"]<min_len:
                return
            lo,hi=parts[0]["lo"],parts[-1]["hi"];value=sum(p["value"] for p in parts)/len(parts)
            p1,p2=([lo,value],[hi,value]) if parts[0]["axis"]=="h" else ([value,lo],[value,hi])
            native.append({"axis":parts[0]["axis"],"value":value,"lo":lo,"hi":hi,"p1":p1,"p2":p2,
                           "paths":sorted({i for p in parts for i in p["paths"]}),"filled_strip":False,
                           "dashed_assembly":True})
        for s in sorted(values,key=lambda x:x["lo"]):
            if chain and s["lo"]-chain[-1]["hi"]>.25:
                emit(chain);chain=[]
            chain.append(s)
        emit(chain)
    merged = []
    for s in sorted(native,key=lambda x:(x["axis"],round(x["value"],1),x["lo"])):
        found = next((t for t in merged if t["axis"]==s["axis"] and abs(t["value"]-s["value"])<=.35
                      and abs(t["lo"]-s["lo"])<=.4 and abs(t["hi"]-s["hi"])<=.4), None)
        if found:
            found["paths"] = sorted(set(found["paths"]+s["paths"]))
        else:
            merged.append(s)
    for i,s in enumerate(merged):
        s.update(id=f"p{page.number+1}:g{i+1}",value=round(s["value"],4),lo=round(s["lo"],4),hi=round(s["hi"],4))
    return merged


def grouped_tags(annotations):
    groups=[]
    tags=sorted((a for a in annotations if a["kind"]=="circuit_tag"),key=lambda a:center(a["bbox"])[0])
    for a in tags:
        x,y=center(a["bbox"]);h=a["bbox"][3]-a["bbox"][1]
        group=next((g for g in groups if abs(g["x"]-x)<=max(h*1.3,5)),None)
        if group is None:
            group={"x":x,"tags":[]};groups.append(group)
        group["tags"].append(a)
    result=[]
    for g in groups:
        tags=sorted(g["tags"],key=lambda a:center(a["bbox"])[1])
        if len(tags)<3:
            continue
        gaps=[center(b["bbox"])[1]-center(a["bbox"])[1] for a,b in zip(tags,tags[1:])]
        positive=[x for x in gaps if x>1]
        spacing=median(positive) if positive else 0
        if not spacing:
            continue
        part=[]
        for a in tags:
            if part and center(a["bbox"])[1]-center(part[-1]["bbox"])[1]>spacing*3:
                if len(part)>=3:
                    result.append({"x":g["x"],"tags":part})
                part=[]
            part.append(a)
        if len(part)>=3:
            result.append({"x":g["x"],"tags":part})
    return result


def associate_branches(annotations, geometry):
    """Associate only when literal circuit tags and a vector bus both exist.

    This is a horizontal single-line-diagram recognizer, not a complete netlist
    solver. Open switch contacts are not incorrectly interpreted as wire breaks.
    """
    associations=[];groups=[];issues=[]
    hlines=[x for x in geometry if x["axis"]=="h" and not x.get("dashed_assembly")]
    vlines=[x for x in geometry if x["axis"]=="v" and not x.get("dashed_assembly")]
    for group in grouped_tags(annotations):
        tags=group["tags"]
        heights=[a["bbox"][3]-a["bbox"][1] for a in tags]
        fh=median(heights)
        rows=[]
        for tag in tags:
            tb=tag["bbox"]
            candidates=[s for s in hlines if s["hi"]>=tb[0]-fh and s["lo"]<tb[0]-fh*3
                        and tb[3]-.2*fh<=s["value"]<=tb[3]+fh*1.5]
            if not candidates:
                continue
            best=min(candidates,key=lambda s:abs(s["value"]-tb[3]))
            rows.append({"tag":tag,"baseline":best["value"],"tag_wire":best["id"]})
        if len(rows)<3:
            issues.append({"type":"unresolved_circuit_geometry","anchor_ids":[a["id"] for a in tags],
                           "message":"回路编号已读取，但未找到足够的对应矢量出线；需局部图像识别"})
            continue
        spacing=median([b["baseline"]-a["baseline"] for a,b in zip(rows,rows[1:])])
        candidates=[]
        for v in vlines:
            if v["value"]>=group["x"]-fh*5:
                continue
            touched=[]
            for row in rows:
                y=row["baseline"]
                if not v["lo"]-1<=y<=v["hi"]+1:
                    continue
                ends=[s for s in hlines if abs(s["value"]-y)<=1
                      and abs(s["lo"]-v["value"])<=max(1,fh*.2) and s["hi"]>v["value"]+fh*.6]
                if ends:
                    touched.append(row["tag"]["id"])
            if len(touched)>=max(3,math.ceil(len(rows)*.6)):
                candidates.append((len(touched),v,touched))
        if not candidates:
            issues.append({"type":"unresolved_bus","anchor_ids":[a["id"] for a in tags],
                           "message":"未找到与回路出线端点一致的母线，不按文字纵坐标强行关联"})
            continue
        candidates.sort(key=lambda x:(-x[0],x[1]["value"]))
        best=candidates[0];bus=best[1]
        # Distinct equally supported spines make row membership unsafe.
        if any(c[0]==best[0] and abs(c[1]["value"]-bus["value"])>fh for c in candidates[1:]):
            issues.append({"type":"ambiguous_bus","message":"存在多条同等支持的母线，保留回路标注等待补核"})
            continue
        oblique=[g for g in geometry if g["axis"]=="o"]
        symbol_rows=set()
        for ri,row in enumerate(rows):
            for a in annotations:
                if a["kind"] not in ("device_model","unclassified_model"):
                    continue
                b=a["bbox"];fs=a["font_size"]
                if not bus["value"]<=center(b)[0]<group["x"]-fh or abs(center(b)[1]-row["baseline"])>spacing*.55:
                    continue
                for g in oblique:
                    x=(g["p1"][0]+g["p2"][0])/2;y=(g["p1"][1]+g["p2"][1])/2
                    if b[0]-fs<=x<=b[2]+fs and b[3]-fs*.2<=y<=b[3]+fs*2 and abs(y-row["baseline"])<fs*2:
                        symbol_rows.add(ri);break
        if len(symbol_rows)<2:
            issues.append({"type":"circuit_symbol_support_missing","message":"编号和直线可能来自表格；未找到至少两行开关符号依据，不作为单线回路图处理"})
            continue
        gid=f"circuit_group_{len(groups)+1}"
        groups.append({"id":gid,"bus_geometry":bus["id"],"bus_x":bus["value"],"terminal_tag_x":group["x"],
                       "method":"literal circuit tags + vector branch endpoints + common bus"})
        for i,row in enumerate(rows):
            y=row["baseline"]
            top=(rows[i-1]["baseline"]+y)/2 if i else y-spacing/2
            bottom=(rows[i+1]["baseline"]+y)/2 if i+1<len(rows) else y+spacing/2
            items=[];phases=[];wires=[];loads=[];others=[]
            for a in annotations:
                x,cy=center(a["bbox"])
                if not top<=cy<bottom:
                    continue
                if bus["value"]<=x<group["x"]-fh:
                    if a["kind"] in ("device_model","unclassified_model","voltage_ratio","device_tag"):
                        items.append(a["id"])
                    elif a["kind"]=="phase":
                        phases.append(a["id"])
                    elif a["kind"]=="cable":
                        wires.append(a["id"])
                    else:
                        others.append(a["id"])
                elif group["x"]+fh*.4<=x<group["x"]+fh*25 and a["kind"] in ("other","calculation"):
                    # These are load-text candidates, not additional equipment.
                    loads.append(a["id"])
            items.sort(key=lambda aid:center(next(a for a in annotations if a["id"]==aid)["bbox"])[0])
            associations.append({"id":f"{gid}:r{i+1}","group_id":gid,"circuit_tag":row["tag"]["raw"],
                                 "tag_anchor":row["tag"]["id"],"wire_geometry":row["tag_wire"],
                                 "baseline":y,"band":[top,bottom],"bus_x":bus["value"],"tag_x":group["x"],
                                 "device_annotation_ids":items,"phase_anchor_ids":phases,"wire_annotation_ids":wires,
                                 "load_annotation_ids":loads,"other_annotation_ids":others,"function_annotation_ids":[],
                                 "status":"native_geometry_association","electrical_connectivity_verified":False})
    # Functional captions can sit above a neighbouring wire. Associate a caption
    # only to one aligned, close device tag already placed on a supported branch.
    # This does not merge a timer and a contactor or decide physical quantities.
    for caption in [a for a in annotations if a["kind"]=="function_label"]:
        x,y=center(caption["bbox"]);candidates=[]
        for row in associations:
            for aid in row["device_annotation_ids"]:
                tag=next(a for a in annotations if a["id"]==aid)
                if tag["kind"]!="device_tag":
                    continue
                tx,ty=center(tag["bbox"])
                if abs(tx-x)<caption["font_size"]*2 and 0<ty-y<caption["font_size"]*4:
                    candidates.append((math.hypot(tx-x,ty-y),row))
        candidates.sort(key=lambda x:x[0])
        if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0]>caption["font_size"]):
            for row in associations:
                if caption["id"] in row["other_annotation_ids"]:
                    row["other_annotation_ids"].remove(caption["id"])
            candidates[0][1]["function_annotation_ids"].append(caption["id"])
    return groups,associations,issues


def associate_separate_parameters(annotations, branches):
    by_id={a["id"]:a for a in annotations};links=[];issues=[]
    for a in annotations:
        if a["kind"] not in ("residual_current","apparent_power"):
            continue
        row=next((r for r in branches if a["id"] in r["other_annotation_ids"] or a["id"] in r["device_annotation_ids"]),None)
        if row is None:
            continue
        target_kinds=("device_model",) if a["kind"]=="residual_current" else ("voltage_ratio",)
        candidates=[]
        for aid in row["device_annotation_ids"]:
            other=by_id[aid]
            if other["kind"] not in target_kinds:
                continue
            if a["kind"]=="residual_current" and other["explicit_parameters"].get("drawing_abbreviation") not in ("MCB","RCB","RCBO","RCCB","RCD"):
                continue
            x,y=center(a["bbox"]);ox,oy=center(other["bbox"])
            width=other["bbox"][2]-other["bbox"][0]
            if abs(x-ox)>max(width*.8,a["font_size"]*2):
                continue
            candidates.append((math.hypot(x-ox,y-oy),other))
        candidates.sort(key=lambda c:c[0])
        if not candidates:
            continue
        if len(candidates)>1 and candidates[1][0]-candidates[0][0]<a["font_size"]:
            issues.append({"type":"ambiguous_parameter_owner","anchor_ids":[a["id"]],"message":"独立参数存在多个近邻器件，未强行关联"})
            continue
        links.append({"parameter_anchor":a["id"],"target_anchor":candidates[0][1]["id"],"circuit_id":row["id"],
                      "parameters":a["explicit_parameters"],"method":"same vector-supported branch + exclusive spatial owner"})
    return links,issues


def native_shapes(page,geometry):
    shapes=[]
    for pid,p in enumerate(page.get_drawings()):
        for item in p["items"]:
            if item[0] in ("re","qu"):
                rect=(item[1] if item[0]=="re" else item[1].rect)*page.rotation_matrix
                if rect.width>2 and rect.height>2:
                    shapes.append({"id":f"p{page.number+1}:path{pid}","kind":"rectangle","bbox":box(rect)})
        lines=[i for i in p["items"] if i[0]=="l"]
        if len(lines) in (3,4) and (len(lines)==4 or p.get("closePath")):
            points=[pt for line in lines for pt in line[1:3]]
            xs={round(q.x,1) for q in points};ys={round(q.y,1) for q in points}
            if len(xs)==2 and len(ys)==2 and all(min(abs(i[1].x-i[2].x),abs(i[1].y-i[2].y))<.1 for i in lines):
                rect=p["rect"]*page.rotation_matrix
                if rect.width>2 and rect.height>2:
                    shapes.append({"id":f"p{page.number+1}:path{pid}","kind":"rectangle","bbox":box(rect)})
        if sum(i[0]=="c" for i in p["items"])>=2:
            rect=p["rect"]*page.rotation_matrix
            if min(rect.width,rect.height)>2 and max(rect.width,rect.height)<30:
                shapes.append({"id":f"p{page.number+1}:path{pid}","kind":"curve_group","bbox":box(rect)})
    horizontal=[g for g in geometry if g["axis"]=="h"]
    vertical=[g for g in geometry if g["axis"]=="v"]
    for i,top in enumerate(horizontal):
        for bottom in horizontal[i+1:]:
            if not 2<bottom["value"]-top["value"]<80:
                continue
            if abs(top["lo"]-bottom["lo"])>.7 or abs(top["hi"]-bottom["hi"])>.7:
                continue
            sides=[]
            for x in (top["lo"],top["hi"]):
                side=next((g for g in vertical if abs(g["value"]-x)<.7 and abs(g["lo"]-top["value"])<.7
                           and abs(g["hi"]-bottom["value"])<.7),None)
                if side:
                    sides.append(side)
            if len(sides)==2:
                shapes.append({"id":f"p{page.number+1}:rect:{top['id']}:{bottom['id']}","kind":"rectangle",
                               "bbox":[top["lo"],top["value"],top["hi"],bottom["value"]],
                               "constituent_geometry_ids":[top["id"],bottom["id"]]+[g["id"] for g in sides]})
    return shapes


def symbol_backed_markings(annotations,geometry,shapes,branches,parameter_links):
    """Locate native model labels beside actual symbol geometry.

    Output remains a diagram instance, not a catalogue selection or globally
    deduplicated equipment count. Device tags and function captions remain
    distinct; two symbolic roles are never merged into one purchased unit.
    """
    output=[]
    by_id={a["id"]:a for a in annotations}
    for a in annotations:
        if a["kind"] not in ("device_model","unclassified_model","power_marking","voltage_ratio","device_tag"):
            continue
        b=a["bbox"];cx,cy=center(b);fs=a["font_size"]
        rectangles=[s for s in shapes if s["kind"]=="rectangle" and
                    s["bbox"][0]<=cx<=s["bbox"][2] and s["bbox"][1]<=cy<=s["bbox"][3] and
                    (s["bbox"][2]-s["bbox"][0])*(s["bbox"][3]-s["bbox"][1])<max((b[2]-b[0])*(b[3]-b[1])*15,fs*fs*70)]
        rectangles.sort(key=lambda s:(s["bbox"][2]-s["bbox"][0])*(s["bbox"][3]-s["bbox"][1]))
        strokes=[]
        for g in geometry:
            if g["axis"]!="o":
                continue
            x,y=(g["p1"][0]+g["p2"][0])/2,(g["p1"][1]+g["p2"][1])/2
            length=math.dist(g["p1"],g["p2"])
            if b[0]-fs<=x<=b[2]+fs and b[3]-fs*.2<=y<=b[3]+fs*2 and fs*.4<length<fs*5:
                strokes.append(g["id"])
        curves=[]
        if a["kind"]=="voltage_ratio":
            for s in shapes:
                x,y=center(s["bbox"])
                if s["kind"]=="curve_group" and b[0]-fs<=x<=b[2]+fs and b[3]-fs*.5<y<b[3]+fs*3:
                    curves.append(s["id"])
        if a["kind"]=="power_marking" and not rectangles:
            continue
        owner=next((r for r in branches if a["id"] in r["device_annotation_ids"]),None)
        params=dict(a["explicit_parameters"])
        param_ids=[]
        for link in parameter_links:
            if link["target_anchor"]==a["id"]:
                params.update(link["parameters"]);param_ids.append(link["parameter_anchor"])
        evidence=strokes+([rectangles[0]["id"]] if rectangles else [])+curves
        output.append({"id":a["id"]+":symbol","label_anchor":a["id"],"raw_label":a["raw"],
                       "literal_kind":a["kind"],"literal_meaning":a["meaning"],"explicit_parameters":params,
                       "parameter_anchor_ids":param_ids,
                       "geometry_ids":list(dict.fromkeys(evidence)),"enclosure_bbox":rectangles[0]["bbox"] if rectangles else None,
                       "circuit_id":owner["id"] if owner else None,"circuit_tag":owner["circuit_tag"] if owner else None,
                       "status":"native_label_with_nearby_symbol_geometry" if evidence else "native_label_requires_symbol_review",
                       "physical_quantity":None,"terminals_verified":False})

    # Transfer blocks expose several ports; preserve the incoming devices as
    # separate diagram instances even though they share a model designation.
    transfer_links=[]
    hlines=[g for g in geometry if g["axis"]=="h"]
    for transfer in output:
        if transfer["explicit_parameters"].get("drawing_abbreviation")!="ATS" or not transfer["enclosure_bbox"]:
            continue
        rect=transfer["enclosure_bbox"]
        ports=[g for g in hlines if abs(g["hi"]-rect[0])<1 and rect[1]<g["value"]<rect[3]]
        for port in ports:
            candidates=[]
            for inst in output:
                if inst["explicit_parameters"].get("drawing_abbreviation") not in ("MCCB","MCB","RCB","RCBO","RCCB"):
                    continue
                label=by_id[inst["label_anchor"]];b=label["bbox"];x,y=center(b);fs=label["font_size"]
                if x<rect[0] and b[3]-fs*.3<=port["value"]<=b[3]+fs*2 and x>=port["lo"]-fs*10:
                    candidates.append((abs(port["value"]-b[3]),inst))
            candidates.sort(key=lambda c:c[0])
            if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0]>1):
                transfer_links.append({"upstream_symbol":candidates[0][1]["id"],"transfer_symbol":transfer["id"],
                                       "port_geometry":port["id"],"method":"literal model, symbol position and transfer-block input line",
                                       "terminal_ids":None,"electrical_terminal_wiring_verified":False})
    return output,transfer_links


def recognize(path,ocr_mode="off",tessdata=None,ocr_engine="auto"):
    path=Path(path)
    if path.suffix.lower()!=".pdf":
        raise ValueError("本轮识别器直接处理PDF；DWG请使用经验证的CAD解析器，不能改扩展名代替解码")
    sid=hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    doc=fitz.open(path)
    if doc.needs_pass:
        raise ValueError("PDF需要密码")
    if not 0<len(doc)<=200:
        raise ValueError("当前单次处理1至200页；超出需分批，不截断冒充完整")
    result={"version":VERSION,"filename":path.name,"source_id":sid,"pages":[],"issues":[],
            "source_modified":False,"database_accessed":False,"model_calls":0,"engineering_verified":False,
            "scope":"PDF原生标注与有矢量依据的水平单线图回路关联；未宣称完成全图符号识别、物理数量或全拓扑重建"}
    for pg in doc:
        annotations=native_annotations(pg,sid)
        native_count=len(annotations)
        ocr_info=None;ocr_issue=None
        from ocr import needs_ocr,tesseract,rapidocr
        image_area=sum(fitz.Rect(i["bbox"]).get_area() for i in pg.get_image_info())
        if needs_ocr(annotations,len(pg.get_drawings()),min(1,image_area/max(1,pg.rect.get_area()))):
            if ocr_mode in ("auto","on"):
                try:
                    if ocr_engine=="tesseract":
                        ocr_rows,ocr_info=tesseract(pg,sid,tessdata)
                    else:
                        try:ocr_rows,ocr_info=rapidocr(pg,sid)
                        except RuntimeError:
                            if ocr_engine=="rapidocr":raise
                            ocr_rows,ocr_info=tesseract(pg,sid,tessdata)
                    annotations.extend(ocr_rows)
                except (RuntimeError,TimeoutError) as exc:
                    ocr_issue={"type":"ocr_required_but_failed","message":str(exc)}
            else:
                ocr_issue={"type":"ocr_required","message":"原生文字不足，可能为扫描图或转曲字体，需启用OCR与局部视觉识别"}
        geometry=segments(pg,annotations)
        groups,branches,issues=associate_branches(annotations,geometry)
        links,parameter_issues=associate_separate_parameters(annotations,branches)
        shapes=native_shapes(pg,geometry)
        instances,transfer_links=symbol_backed_markings(annotations,geometry,shapes,branches,links)
        from schedules import extract
        schedules=extract(annotations,geometry)
        allissues=issues+parameter_issues
        if ocr_issue:
            allissues.append(ocr_issue)
        if ocr_info:
            allissues.append({"type":"ocr_transcription_pending_review","message":"本地OCR已运行；转写仍需与原图核对，不等同于电气符号或连接关系识别完成"})
        if not annotations:
            allissues.append({"type":"raster_or_outlined_text","message":"未发现可读原生文字，需要OCR与局部视觉识别；未输出伪造文字"})
        mixed=any(any(k in a["raw"] for k in ("挂脚示意图","二层门示意图","安装尺寸","开孔图")) for a in annotations)
        if mixed:
            allissues.append({"type":"mixed_views","message":"检测到结构/布置与接线混合图面；不同视图可能重复表示设备，未累计采购数量"})
        if not branches:
            allissues.append({"type":"topology_pending","message":"未提取到本算法支持的水平单线回路；原文已保留，需专用分区/符号识别"})
        page={"page":pg.number+1,"size":box(pg.rect),"rotation":pg.rotation,"coordinate_system":"display_page_points_top_left",
              "native_annotation_count":native_count,"ocr":ocr_info,
              "annotations":annotations,"geometry":geometry,"circuit_groups":groups,"branches":branches,
              "parameter_links":links,"shapes":shapes,"diagram_instances":instances,"transfer_input_links":transfer_links,
              "embedded_schedules":schedules,
              "mixed_views":mixed,"issues":allissues}
        result["pages"].append(page)
        result["issues"].extend(dict(x,page=pg.number+1) for x in allissues)
    result["summary"]={"pages":len(result["pages"]),"native_annotations":sum(p["native_annotation_count"] for p in result["pages"]),
                       "ocr_annotations":sum(len(p["annotations"])-p["native_annotation_count"] for p in result["pages"]),
                       "vector_segments":sum(len(p["geometry"]) for p in result["pages"]),
                       "associated_circuits":sum(len(p["branches"]) for p in result["pages"]),
                       "separate_parameter_links":sum(len(p["parameter_links"]) for p in result["pages"]),
                       "symbol_backed_markings":sum(bool(x["geometry_ids"]) for p in result["pages"] for x in p["diagram_instances"]),
                       "embedded_schedule_rows":sum(len(s["rows"]) for p in result["pages"] for s in p["embedded_schedules"]),
                       "annotation_kinds":dict(Counter(a["kind"] for p in result["pages"] for a in p["annotations"])),
                       "recognition_complete":False,"purchase_quantities_confirmed":False}
    return result


def write_reports(result,path,out):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment,Font,PatternFill
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/"recognition.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    wb=Workbook();ws=wb.active;ws.title="回路关联"
    ws.append(["页码","回路号","相别原文","器件标注原文","功能说明原文","独立参数关联","线路原文","负荷原文","证据编号","状态"])
    for p in result["pages"]:
        lookup={a["id"]:a for a in p["annotations"]}
        for r in p["branches"]:
            texts=lambda key:" | ".join(lookup[x]["raw"] for x in r[key])
            pars=[x for x in p["parameter_links"] if x["circuit_id"]==r["id"]]
            ws.append([p["page"],r["circuit_tag"],texts("phase_anchor_ids"),texts("device_annotation_ids"),texts("function_annotation_ids"),
                       " | ".join(lookup[x["target_anchor"]]["raw"]+" ← "+lookup[x["parameter_anchor"]]["raw"] for x in pars),
                       texts("wire_annotation_ids"),texts("load_annotation_ids"),r["tag_anchor"],"有原生几何依据；全连接仍待校核"])
    ws=wb.create_sheet("图面器件标注")
    ws.append(["页码","实例编号","型号/标记原文","常用含义","明确参数","所属回路","符号图元依据","状态","采购数量"])
    for p in result["pages"]:
        for a in p["diagram_instances"]:
            ws.append([p["page"],a["id"],a["raw_label"],a["literal_meaning"],json.dumps(a["explicit_parameters"],ensure_ascii=False),
                       a["circuit_tag"],json.dumps(a["geometry_ids"]),a["status"],"未确定，不以标注出现次数代替"])
    ws=wb.create_sheet("全部文字与证据")
    ws.append(["页码","原文编号","原文","分类","明确参数","位置（显示页坐标）","所属回路","文字来源","OCR分数（非准确率）"])
    for p in result["pages"]:
        for a in p["annotations"]:
            owner=[r["circuit_tag"] for r in p["branches"] if a["id"] in sum((r[k] for k in ("device_annotation_ids","phase_anchor_ids","wire_annotation_ids","load_annotation_ids","other_annotation_ids","function_annotation_ids")),[])]
            ws.append([p["page"],a["id"],a["raw"],a["kind"],json.dumps(a["explicit_parameters"],ensure_ascii=False),
                       json.dumps(a["bbox"])," | ".join(owner),a["source"],a.get("ocr_confidence")])
    ws=wb.create_sheet("图内明示清单")
    ws.append(["页码","来源表","元件名称原文","型号规格原文","来源表数量","单位原文","供应范围标记","文字来源","确认状态"])
    for p in result["pages"]:
        for table in p["embedded_schedules"]:
            for r in table["rows"]:
                ws.append([p["page"],table["id"],r["name_raw"],r["model_raw"],r["quantity_literal"],r["unit_literal"],
                           r["supply_scope_marking"],"OCR待核对" if not r["transcription_verified"] else "原生文字",
                           "原图内清单，尚非已确认报价表"])
    ws=wb.create_sheet("识别边界与待核对")
    ws.append(["页码","类型","内容"])
    ws.append([None,"scope",result["scope"]])
    for issue in result["issues"]:
        ws.append([issue.get("page"),issue["type"],issue["message"]])
    for ws in wb:
        ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width=30
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str):cell.data_type="s"
                cell.alignment=Alignment(vertical="top",wrap_text=True)
        for c in ws[1]:c.font=Font(color="FFFFFF",bold=True);c.fill=PatternFill("solid",fgColor="234F6B")
    wb.save(out/"recognition.xlsx")
    doc=fitz.open(path)
    for info in result["pages"]:
        pg=doc[info["page"]-1]
        for a in info["annotations"]:
            if a["kind"] not in ("device_model","circuit_tag","residual_current","unclassified_model"):
                continue
            rect=fitz.Rect(a["bbox"])*pg.derotation_matrix
            annot=pg.add_rect_annot(rect)
            annot.set_colors(stroke=(1,0,0) if a["kind"]=="device_model" else (0,.3,1))
            annot.set_info(content=a["id"]+"\n"+a["raw"])
            annot.update()
    doc.save(out/"source_with_evidence.pdf")


def main():
    parser=argparse.ArgumentParser(description="PDF原生电气标注和回路关联验证器")
    parser.add_argument("pdf");parser.add_argument("--out",required=True)
    parser.add_argument("--ocr",choices=("off","auto","on"),default="off")
    parser.add_argument("--tessdata",help="可选本地Tesseract模型目录，含eng与chi_sim语言包")
    parser.add_argument("--ocr-engine",choices=("auto","rapidocr","tesseract"),default="auto")
    parser.add_argument("--prepare-vision",action="store_true",help="生成局部识图图片和原文证据，不调用API")
    parser.add_argument("--vision",action="store_true",help="实际调用配置的视觉API；密钥只从环境或隐藏输入读取")
    args=parser.parse_args();dest=Path(args.out)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("输出目录已有文件，请选择一个新目录以保留原有结果")
    result=recognize(args.pdf,args.ocr,args.tessdata,args.ocr_engine);write_reports(result,args.pdf,dest)
    if args.prepare_vision or args.vision:
        from vision import prepare,run,collect
        import os
        manifest=prepare(args.pdf,result,dest/"vision")
        print("视觉分区数：",len(manifest["units"]),flush=True)
        if args.vision:
            key=os.getenv("DRAWING_VISION_API_KEY","")
            if not key:
                import getpass
                key=getpass.getpass("输入识图API密钥（不回显、不保存）：")
            base=os.getenv("DRAWING_VISION_BASE_URL","https://dashscope.aliyuncs.com/compatible-mode/v1")
            model=os.getenv("DRAWING_VISION_MODEL","qwen3-vl-plus")
            report=run(manifest,dest/"vision",key,base,model)
            collect(manifest,dest/"vision",base,model)
            print(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(result["summary"],ensure_ascii=False,indent=2))
    print("结果目录：",dest.resolve())


if __name__=="__main__":
    main()
