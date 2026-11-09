"""Bounded, read-only native PDF text placement; no network or third-party dependencies.

Supported: unencrypted page trees, ObjStm, Flate/ASCII filters, Type0 ToUnicode
and simple Latin fonts, text matrices, page rotation, and Form XObjects.
Unsupported encodings/operators produce diagnostics rather than fabricated text.
This is a text-position reader, not a replacement for a PDF renderer or a circuit solver.
"""
import base64
import binascii
import json
import math
import re
import zlib


def pdf_tokens(data):
    out=[];i=0;n=len(data);space=b'\x00\t\n\f\r '
    while i<n:
        c=data[i]
        if c in space:i+=1;continue
        if c==37:
            end=data.find(b'\n',i);i=n if end<0 else end+1;continue
        if data[i:i+2] in (b'<<',b'>>'):
            out.append(data[i:i+2].decode());i+=2;continue
        if c in (91,93):out.append(chr(c));i+=1;continue
        if c==40:
            value=bytearray();depth=1;i+=1
            while i<n and depth:
                c=data[i];i+=1
                if c==92:
                    if i>=n:raise ValueError('PDF string escape incomplete')
                    c=data[i];i+=1
                    if c in b'01234567':
                        digits=bytes([c])
                        while len(digits)<3 and i<n and data[i] in b'01234567':digits+=data[i:i+1];i+=1
                        value.append(int(digits,8)&255)
                    elif c in (10,13):
                        if c==13 and i<n and data[i]==10:i+=1
                    else:value.append({110:10,114:13,116:9,98:8,102:12}.get(c,c))
                elif c==40:depth+=1;value.append(c)
                elif c==41:
                    depth-=1
                    if depth:value.append(c)
                else:value.append(c)
            if depth:raise ValueError('PDF string incomplete')
            out.append(bytes(value));continue
        if c==60:
            end=data.find(b'>',i+1)
            if end<0:raise ValueError('PDF hex string incomplete')
            raw=re.sub(rb'\s+',b'',data[i+1:end]);raw+=b'0' if len(raw)%2 else b''
            out.append(bytes.fromhex(raw.decode('ascii')));i=end+1;continue
        start=i;i+=1
        while i<n and data[i] not in space+b'()<>[]{}/%':i+=1
        token=data[start:i]
        if c==47:
            # A name starts with '/', which is itself a delimiter.
            i=start+1;start=i
            while i<n and data[i] not in space+b'()<>[]{}/%':i+=1
            token=b'/'+data[start:i]
            token=re.sub(rb'#([0-9A-Fa-f]{2})',lambda m:bytes([int(m[1],16)]),token)
            out.append(token.decode('latin1'));continue
        if re.fullmatch(rb'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',token):
            out.append(float(token) if b'.' in token else int(token))
        elif token==b'true':out.append(True)
        elif token==b'false':out.append(False)
        elif token==b'null':out.append(None)
        else:out.append(token.decode('latin1'))
        if len(out)>1200000:raise ValueError('PDF token budget exceeded')
    return out


def pdf_value(tokens,index=0):
    if index>=len(tokens):raise ValueError('PDF value incomplete')
    x=tokens[index];index+=1
    if x=='<<':
        d={}
        while index<len(tokens) and tokens[index]!='>>':
            key=tokens[index];index+=1
            if not isinstance(key,str) or not key.startswith('/'):raise ValueError('Invalid PDF dictionary key')
            value,index=pdf_value(tokens,index);d[key]=value
        if index>=len(tokens):raise ValueError('PDF dictionary incomplete')
        return d,index+1
    if x=='[':
        a=[]
        while index<len(tokens) and tokens[index]!=']':
            v,index=pdf_value(tokens,index);a.append(v)
        if index>=len(tokens):raise ValueError('PDF array incomplete')
        return a,index+1
    if type(x) is int and index+1<len(tokens) and type(tokens[index]) is int and tokens[index+1]=='R':
        return ('ref',x,tokens[index]),index+2
    return x,index


def pdf_resolve(objects,value):
    seen=set()
    while isinstance(value,tuple) and value[:1]==('ref',):
        if value[1] in seen:raise ValueError('Cyclic PDF reference')
        seen.add(value[1]);value=objects.get(value[1],{}).get('value')
    return value


def pdf_stream(objects,entry):
    data=entry.get('stream',b'');d=entry.get('value',{})
    filters=pdf_resolve(objects,d.get('/Filter',[]))
    if isinstance(filters,str):filters=[filters]
    for f in filters or []:
        if f in ('/FlateDecode','/Fl'):
            inflater=zlib.decompressobj();data=inflater.decompress(data,12000001)
            if len(data)>12000000 or inflater.unconsumed_tail:raise ValueError('PDF stream expansion exceeds budget')
        elif f in ('/ASCIIHexDecode','/AHx'):
            raw=re.sub(rb'\s+',b'',data).rstrip(b'>');data=bytes.fromhex((raw+(b'0' if len(raw)%2 else b'')).decode())
        elif f in ('/ASCII85Decode','/A85'):
            raw=data.strip()
            data=base64.a85decode(raw,adobe=True) if raw.startswith(b'<~') else base64.a85decode(raw[:-2] if raw.endswith(b'~>') else raw)
        else:raise ValueError('Unsupported PDF stream filter '+str(f))
    if len(data)>12000000:raise ValueError('PDF stream exceeds budget')
    return data


def pdf_objects(data):
    if not data.startswith(b'%PDF-'):raise ValueError('Not a PDF file')
    objects={}
    for m in re.finditer(rb'(?m)(?<!\S)(\d+)\s+(\d+)\s+obj\b',data):
        start=m.end();end=data.find(b'endobj',start)
        if end<0:raise ValueError('PDF object incomplete')
        # Dictionary tokens never include binary stream payloads.
        mark=re.search(rb'\bstream(?:\r\n|\n|\r)',data[start:end])
        head=data[start:start+mark.start()] if mark else data[start:end]
        value,_=pdf_value(pdf_tokens(head))
        entry={'value':value}
        if mark:
            begin=start+mark.end();length=value.get('/Length') if isinstance(value,dict) else None
            finish=begin+length if type(length) is int else data.find(b'endstream',begin)
            if finish<begin:raise ValueError('PDF stream incomplete')
            entry['stream']=data[begin:finish]
        objects[int(m[1])]=entry
        if len(objects)>15000:raise ValueError('PDF object budget exceeded')
    for entry in list(objects.values()):
        d=entry['value']
        if isinstance(d,dict) and d.get('/Type')=='/ObjStm':
            raw=pdf_stream(objects,entry);first=d.get('/First');count=d.get('/N')
            if type(first) is not int or type(count) is not int or not 0<=count<=15000:raise ValueError('Invalid PDF object stream')
            header=[int(x) for x in raw[:first].split()]
            if len(header)!=2*count:raise ValueError('PDF object stream index mismatch')
            for i in range(count):
                number,offset=header[2*i:2*i+2];stop=header[2*i+3] if i+1<count else len(raw)-first
                value,_=pdf_value(pdf_tokens(raw[first+offset:first+stop]));objects.setdefault(number,{'value':value})
    for entry in objects.values():
        d=entry.get('value')
        if isinstance(d,dict) and d.get('/Filter')=='/Standard':raise ValueError('Encrypted PDFs are not supported by native reader')
    return objects


def pdf_cmap(data):
    result={};text=data.decode('latin1')
    for block in re.findall(r'beginbfchar(.*?)endbfchar',text,re.S):
        for a,b in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>',block):
            result[bytes.fromhex(a)]=bytes.fromhex(b).decode('utf-16-be')
    for block in re.findall(r'beginbfrange(.*?)endbfrange',text,re.S):
        for m in re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f]+)>|\[(.*?)\])',block,re.S):
            a,b=int(m[1],16),int(m[2],16);size=len(m[1])//2
            if b-a>65536:raise ValueError('PDF Unicode range exceeds budget')
            values=re.findall(r'<([0-9A-Fa-f]+)>',m[5]) if m[5] is not None else None
            for offset,code in enumerate(range(a,b+1)):
                if values is not None:
                    if offset>=len(values):raise ValueError('PDF Unicode range mismatch')
                    dest=bytes.fromhex(values[offset])
                else:
                    dest=(int(m[4],16)+offset).to_bytes(len(m[4])//2,'big')
                result[code.to_bytes(size,'big')]=dest.decode('utf-16-be')
    return result


def pdf_font(objects,value):
    d=pdf_resolve(objects,value) or {};mapping={};widths={}
    unicode_ref=d.get('/ToUnicode');err=''
    if isinstance(unicode_ref,tuple):mapping=pdf_cmap(pdf_stream(objects,objects[unicode_ref[1]]))
    subtype=d.get('/Subtype');cid=pdf_resolve(objects,(d.get('/DescendantFonts') or [None])[0]) if subtype=='/Type0' else d
    cid=cid or {};default=cid.get('/DW',1000 if subtype=='/Type0' else 500)
    w=pdf_resolve(objects,cid.get('/W',[])) or [];i=0
    while i<len(w):
        start=w[i];value=pdf_resolve(objects,w[i+1]);i+=2
        if isinstance(value,list):
            for j,v in enumerate(value):widths[start+j]=v
        else:
            finish=value;v=w[i];i+=1
            if finish-start>65536:raise ValueError('PDF font width range exceeds budget')
            widths.update({code:v for code in range(start,finish+1)})
    simple=pdf_resolve(objects,d.get('/Widths',[])) or []
    for i,value in enumerate(simple):widths[d.get('/FirstChar',0)+i]=value
    desc=pdf_resolve(objects,cid.get('/FontDescriptor')) or {}
    if subtype=='/Type0' and not mapping:err='CID font without supported ToUnicode map'
    if subtype=='/Type0' and d.get('/Encoding')!='/Identity-H':err='CID encoding requires a supported Identity-H map'
    if subtype=='/Type3':err='Type3 font text metrics are unsupported'
    return {'map':mapping,'widths':widths,'default':default,'ascent':desc.get('/Ascent',800),
            'descent':desc.get('/Descent',-200),'encoding':d.get('/Encoding'), 'error':err}


def pdf_decode(font,raw):
    mapping=font['map'];out=[]
    if mapping:
        lengths=sorted({len(k) for k in mapping},reverse=True);i=0
        while i<len(raw):
            key=next((raw[i:i+n] for n in lengths if raw[i:i+n] in mapping),None)
            if key is None:raise ValueError('Unmapped PDF character code')
            out.append((mapping[key],int.from_bytes(key,'big')));i+=len(key)
    elif font['error']:raise ValueError(font['error'])
    else:
        enc=font.get('encoding')
        if isinstance(enc,dict) or enc not in (None,'/WinAnsiEncoding','/MacRomanEncoding','/StandardEncoding'):
            raise ValueError('Unsupported simple PDF font encoding')
        if enc in (None,'/StandardEncoding') and any(c>=128 for c in raw):
            raise ValueError('Non-ASCII text requires a supported explicit font encoding')
        codec='mac_roman' if enc=='/MacRomanEncoding' else 'cp1252'
        out=[(bytes([code]).decode(codec),code) for code in raw]
    return out


def pdf_matrix(a,b):
    # Transform with b first, then a; column-vector affine representation.
    return [a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],
            a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]]


def pdf_point(m,x,y):return [m[0]*x+m[2]*y+m[4],m[1]*x+m[3]*y+m[5]]


def pdf_display(point,box,rotation):
    x,y=point[0]-box[0],point[1]-box[1];w,h=box[2]-box[0],box[3]-box[1]
    if rotation==0:a,b,width,height=x,h-y,w,h
    elif rotation==90:a,b,width,height=y,x,h,w
    elif rotation==180:a,b,width,height=w-x,y,w,h
    elif rotation==270:a,b,width,height=h-y,w-x,h,w
    else:raise ValueError('Unsupported page rotation')
    return [1000*a/width,1000*b/height]


def pdf_page_tree(objects):
    catalog=next((e['value'] for e in objects.values() if isinstance(e['value'],dict) and e['value'].get('/Type')=='/Catalog'),None)
    if not catalog:raise ValueError('PDF catalog missing')
    result=[];seen=set()
    def walk(ref,inherited):
        if not isinstance(ref,tuple) or ref[1] in seen:raise ValueError('Invalid or cyclic PDF page tree')
        seen.add(ref[1]);d=pdf_resolve(objects,ref);state=dict(inherited)
        for key in ('/MediaBox','/CropBox','/Resources','/Rotate'):
            if key in d:state[key]=pdf_resolve(objects,d[key])
        if d.get('/Type')=='/Page':result.append((d,state));return
        for child in d.get('/Kids',[]):walk(child,state)
    walk(catalog['/Pages'],{})
    if len(result)>20:raise ValueError('Native reader supports at most 20 pages per batch')
    return result


def pdf_text_page(objects,page,inherited):
    box=inherited.get('/CropBox',inherited.get('/MediaBox'));rotation=int(inherited.get('/Rotate',0))%360
    if not isinstance(box,list) or len(box)!=4 or box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Invalid PDF page box')
    spans=[];warnings=[];lines=[];counter=[0]
    identity=[1,0,0,1,0,0]
    def run(data,resources,initial,depth=0):
        if depth>8:raise ValueError('PDF form recursion exceeds budget')
        fonts={k:pdf_font(objects,v) for k,v in (pdf_resolve(objects,resources.get('/Font',{})) or {}).items()}
        state={'ctm':initial,'font':None,'size':12,'hscale':1,'charspace':0,'wordspace':0,'leading':0,'rise':0,'render':0}
        tm=list(identity);lm=list(identity);stack=[];args=[];path=[]
        tokens=pdf_tokens(data);i=0
        def emit(raw):
            nonlocal tm
            font=fonts.get(state['font'])
            if font is None:raise ValueError('Missing PDF font resource')
            try:chars=pdf_decode(font,raw)
            except (ValueError,UnicodeError) as exc:warnings.append(str(exc));return
            text=''.join(v[0] for v in chars)
            advance=sum(font['widths'].get(code,font['default'])*state['size']/1000+state['charspace']+(state['wordspace'] if t==' ' else 0) for t,code in chars)*state['hscale']
            matrix=pdf_matrix(state['ctm'],tm)
            low=font['descent']*state['size']/1000+state['rise'];high=font['ascent']*state['size']/1000+state['rise']
            points=[pdf_display(pdf_point(matrix,x,y),box,rotation) for x,y in ((0,low),(advance,low),(0,high),(advance,high))]
            bbox=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
            if text.strip() and all(math.isfinite(v) for v in bbox):
                if max(bbox[0],0)<min(bbox[2],1000) and max(bbox[1],0)<min(bbox[3],1000):
                    spans.append({'text':text,'bbox':[max(0,bbox[0]),max(0,bbox[1]),min(1000,bbox[2]),min(1000,bbox[3])],
                                  'baseline':pdf_display(pdf_point(matrix,0,state['rise']),box,rotation),
                                  'render_mode':state['render'],'position_source':'pdf_text_matrix_and_font_metrics'})
            tm=pdf_matrix(tm,[1,0,0,1,advance,0])
        while i<len(tokens):
            token=tokens[i]
            if token in ('[','<<'):
                value,i=pdf_value(tokens,i);args.append(value);continue
            i+=1
            if not isinstance(token,str) or token.startswith('/'):
                args.append(token);continue
            op=token
            counter[0]+=1
            if counter[0]>600000:raise ValueError('PDF operator budget exceeded')
            if op=='q':stack.append(dict(state))
            elif op=='Q':
                if not stack:raise ValueError('Unbalanced PDF graphics state')
                state=stack.pop()
            elif op=='cm':state['ctm']=pdf_matrix(state['ctm'],args[-6:])
            elif op=='BT':tm=list(identity);lm=list(identity)
            elif op=='Tf':state['font'],state['size']=args[-2:]
            elif op in ('Tc','Tw','Tz','TL','Ts','Tr'):
                key={'Tc':'charspace','Tw':'wordspace','Tz':'hscale','TL':'leading','Ts':'rise','Tr':'render'}[op]
                state[key]=args[-1]/100 if op=='Tz' else args[-1]
            elif op=='Tm':tm=list(args[-6:]);lm=list(tm)
            elif op in ('Td','TD'):
                x,y=args[-2:]
                if op=='TD':state['leading']=-y
                lm=pdf_matrix(lm,[1,0,0,1,x,y]);tm=list(lm)
            elif op=='T*':lm=pdf_matrix(lm,[1,0,0,1,0,-state['leading']]);tm=list(lm)
            elif op in ('Tj',"'",'"'):
                if op in ("'",'"'):
                    if op=='"':state['wordspace'],state['charspace']=args[-3:-1]
                    lm=pdf_matrix(lm,[1,0,0,1,0,-state['leading']]);tm=list(lm)
                emit(args[-1])
            elif op=='TJ':
                for part in args[-1]:
                    if isinstance(part,bytes):emit(part)
                    elif type(part) in (int,float):tm=pdf_matrix(tm,[1,0,0,1,-part*state['size']*state['hscale']/1000,0])
            elif op=='Do':
                xobjects=pdf_resolve(objects,resources.get('/XObject',{})) or {};ref=xobjects.get(args[-1])
                obj=objects.get(ref[1],{}) if isinstance(ref,tuple) else {};d=obj.get('value',{})
                if d.get('/Subtype')=='/Form':
                    r=pdf_resolve(objects,d.get('/Resources',resources))
                    run(pdf_stream(objects,obj),r,pdf_matrix(state['ctm'],d.get('/Matrix',identity)),depth+1)
            elif op=='m':path=[pdf_point(state['ctm'],*args[-2:])]
            elif op=='l':
                point=pdf_point(state['ctm'],*args[-2:])
                if path:
                    a,b=pdf_display(path[-1],box,rotation),pdf_display(point,box,rotation)
                    if max(abs(a[0]-b[0]),abs(a[1]-b[1]))>=8 and min(abs(a[0]-b[0]),abs(a[1]-b[1]))<=0.1:
                        lines.append([round(v,4) for v in a+b])
                path.append(point)
            elif op in ('S','s','f','F','f*','B','B*','b','b*','n','re','c','v','y'):path=[]
            elif op in ('BI','ID','EI'):raise ValueError('Inline PDF images are unsupported by native reader')
            args=[]
    contents=page.get('/Contents',[])
    if isinstance(contents,tuple):contents=[contents]
    resources=inherited.get('/Resources') or {}
    data=b'\n'.join(pdf_stream(objects,objects[ref[1]]) for ref in contents if isinstance(ref,tuple))
    run(data,resources,identity)
    for i,span in enumerate(spans,1):span['id']='T%d'%i
    if len(spans)>3500:raise ValueError('Native text span budget exceeded')
    # Keep only long straight segments as optional verification evidence, never claim topology.
    unique={tuple(line):line for line in lines}
    return {'spans':spans,'straight_segments':list(unique.values())[:5000],
            'segments_truncated':len(unique)>5000,'warnings':sorted(set(warnings)),
            'rotation':rotation,'status':'available' if spans and not warnings else 'partial' if spans else 'no_native_text'}


def native_pdf_node(encoded):
    try:
        if not isinstance(encoded,str) or len(encoded)>14000000:raise ValueError('PDF Base64 exceeds native reader budget')
        raw=base64.b64decode(''.join(encoded.split()),validate=True)
        if len(raw)>10000000:raise ValueError('PDF exceeds 10 MB native reader budget')
        objects=pdf_objects(raw);pages=[];statuses=[]
        for index,(page,inherited) in enumerate(pdf_page_tree(objects),1):
            try:result=pdf_text_page(objects,page,inherited)
            except (ValueError,KeyError,IndexError,TypeError,OverflowError,UnicodeError) as exc:
                result={'spans':[],'straight_segments':[],'status':'unsupported','warnings':[str(exc)]}
            result['page']=index;statuses.append(result['status'])
            for span in result['spans']:span['id']='P%d-%s'%(index,span['id'])
            # Keep large geometry in compressed, bounded chunks, never a cut JSON string.
            text=json.dumps(result,ensure_ascii=False,separators=(',',':'))
            compact=base64.b64encode(zlib.compress(text.encode())).decode()
            if len(compact)>55000:raise ValueError('Per-page native geometry exceeds Dify variable budget')
            pages.append(compact)
        status='available' if statuses and all(v=='available' for v in statuses) else 'partial' if any(v in ('available','partial') for v in statuses) else 'unavailable'
        return {'native_pages':pages,'native_status':status,'native_message':'' if status=='available' else '部分页面没有可用的原生文字位置，相关页沿用视觉候选并保留人工核对要求。'}
    except (ValueError,KeyError,IndexError,TypeError,OverflowError,UnicodeError,zlib.error,binascii.Error) as exc:
        return {'native_pages':[],'native_status':'unsupported','native_message':'原生定位不可用：'+str(exc)+'；保留视觉识图，不伪造文字位置。'}
