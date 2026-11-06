"""Read literal name/model/quantity cells from a vector-supported parts table.

An embedded table is source data, not an approved purchasing list. OCR cells
retain their confidence and original evidence; quantities are never inferred
from a model's repeated appearances elsewhere on the sheet.
"""
import re
from statistics import median


ALIASES={"name":{"名称","元件名称","元器件名称"},"model":{"型号规格","规格型号","型号","规格"},
         "quantity":{"数量","台数","只数"}}


def clean(s):
    return re.sub(r"[\s:：]","",s)


def center(b):return (b[0]+b[2])/2,(b[1]+b[3])/2


def continuous(geometry,axis):
    buckets={}
    for g in geometry:
        if g["axis"]==axis and not g.get("dashed_assembly"):
            buckets.setdefault(round(g["value"]*4),[]).append(g)
    output=[]
    for members in buckets.values():
        run=None
        for g in sorted(members,key=lambda x:x["lo"]):
            if run is not None and g["lo"]<=run["hi"]+.3:
                run["hi"]=max(run["hi"],g["hi"])
            else:
                if run is not None:output.append(run)
                run=dict(g)
        if run is not None:output.append(run)
    return output


def extract(annotations,geometry):
    headings=[]
    for a in annotations:
        for field,aliases in ALIASES.items():
            if clean(a["raw"]) in aliases:
                headings.append((field,a))
    groups=[]
    for name,anchor in headings:
        if name!="name":continue
        x,y=center(anchor["bbox"]);height=anchor["font_size"]
        selection={"name":anchor}
        for field in ("model","quantity"):
            near=[a for kind,a in headings if kind==field and abs(center(a["bbox"])[1]-y)<=height*1.3]
            if near:selection[field]=min(near,key=lambda a:abs(center(a["bbox"])[0]-x))
        if len(selection)==3:groups.append(selection)
    schedules=[]
    hs=continuous(geometry,"h")
    vs=continuous(geometry,"v")
    for head in groups:
        heady=median(center(a["bbox"])[1] for a in head.values())
        vx=sorted({g["value"] for g in vs if g["lo"]-1<heady<g["hi"]+1})
        bounds={}
        for field,a in head.items():
            x,y=center(a["bbox"]);left=[v for v in vx if v<x];right=[v for v in vx if v>x]
            if left and right:bounds[field]=[max(left),min(right)]
        if len(bounds)!=3 or len({tuple(b) for b in bounds.values()})<3:
            continue
        left=min(b[0] for b in bounds.values());right=max(b[1] for b in bounds.values())
        lines=sorted({g["value"] for g in hs if g["lo"]<left+1 and g["hi"]>right-1})
        fh=median(a["font_size"] for a in head.values())
        options=[]
        for direction,cuts in (("below",[y for y in lines if y>heady]),("above",[y for y in reversed(lines) if y<heady])):
            rows=[]
            for c1,c2 in zip(cuts,cuts[1:]):
                y1,y2=sorted((c1,c2))
                if not 2<y2-y1<fh*12:continue
                edges={v for b in bounds.values() for v in b}
                bounded=all(any(abs(v["value"]-x)<.7 and v["lo"]<=y1+.7 and v["hi"]>=y2-.7 for v in vs) for x in edges)
                if not bounded:
                    if rows:break
                    continue
                cells={}
                for field,(x1,x2) in bounds.items():
                    aa=[a for a in annotations if x1<center(a["bbox"])[0]<x2 and y1<center(a["bbox"])[1]<y2]
                    aa.sort(key=lambda a:(round(center(a["bbox"])[1]/max(1,fh)),a["bbox"][0]))
                    cells[field]={"raw":" ".join(a["raw"] for a in aa),"anchor_ids":[a["id"] for a in aa],
                                  "bbox":[x1,y1,x2,y2],"ocr_used":any(a["source"]!="native_pdf_text" for a in aa),
                                  "minimum_ocr_confidence":min((a["ocr_confidence"] for a in aa if "ocr_confidence" in a),default=None)}
                name=clean(cells["name"]["raw"])
                if name.startswith(("用途","备注","合计","总计")):break
                if not name or not cells["model"]["raw"]:continue
                q=clean(cells["quantity"]["raw"])
                matched=re.fullmatch(r"(\d+)(台|只|个|组|套|件|根)?",q)
                quantity=int(matched[1]) if matched else None
                rows.append({"row":len(rows)+1,"name_raw":cells["name"]["raw"],"model_raw":cells["model"]["raw"],
                             "quantity_literal":quantity,"unit_literal":matched[2] if matched else None,"cells":cells,
                             "quantity_status":"literal" if matched else "requires_transcription_review",
                             "supply_scope_marking":"局供" if "局供" in cells["model"]["raw"] else None,
                             "transcription_verified":not any(c["ocr_used"] for c in cells.values()),
                             "engineering_approved":False})
            options.append((sum(r["quantity_literal"] is not None for r in rows),len(rows),direction,rows))
        _,_,direction,rows=max(options,key=lambda x:(x[0],x[1]))
        if rows:
            schedules.append({"id":f"embedded_schedule_{len(schedules)+1}","header_anchor_ids":{k:a["id"] for k,a in head.items()},
                              "column_bounds":bounds,"body_direction":direction,"rows":rows,"status":"literal_source_table_extraction",
                              "quantity_scope":"该来源表内的明示数量；未与其他视图叠加，未当成已确认报价表"})
    return schedules
