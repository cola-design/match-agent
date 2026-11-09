"""Lossless PNG crop for Dify's standard-library-only code sandbox.

No network, disk access, Pillow, recoloring, or generative image processing.
The input is the 8-bit non-interlaced PNG emitted by PDF-to-Images.
"""
import base64
import itertools
import json
import math
import re
import struct
import zlib


def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def read_png(encoded):
    if not isinstance(encoded, str) or len(encoded) > 16000000:
        raise ValueError('页图编码为空或超过16MB处理预算')
    if encoded.startswith('data:'):
        prefix, encoded = encoded.split(',', 1)
        if prefix != 'data:image/png;base64':
            raise ValueError('必须使用PDF-to-Images的PNG输出')
    encoded = re.sub(r'\s+', '', encoded)
    blob = base64.b64decode(encoded, validate=True)
    if blob[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('输入不是PNG图片')
    offset = 8; packed = []; extras = []; header = None; ended = False
    while offset + 12 <= len(blob):
        size = struct.unpack('>I', blob[offset:offset + 4])[0]
        kind = blob[offset + 4:offset + 8]
        if offset + 12 + size > len(blob):
            raise ValueError('PNG数据被截断')
        data = blob[offset + 8:offset + 8 + size]
        crc = struct.unpack('>I', blob[offset + 8 + size:offset + 12 + size])[0]
        if zlib.crc32(kind + data) & 0xffffffff != crc:
            raise ValueError('PNG校验失败')
        offset += size + 12
        if kind == b'IHDR':
            if header is not None or len(data) != 13:
                raise ValueError('PNG头部无效')
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            packed.append(data)
        elif kind in (b'PLTE', b'tRNS', b'sRGB', b'gAMA', b'cHRM', b'iCCP'):
            extras.append((kind, data))
        elif kind == b'IEND':
            ended = True; break
    if header is None or not ended:
        raise ValueError('PNG结构不完整')
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if depth != 8 or not channels or compression or filtering or interlace:
        raise ValueError('当前裁切仅支持8位非隔行PNG；未擅自转换或忽略该页')
    if width < 1 or height < 1 or width * height > 16000000:
        raise ValueError('页图超过1600万像素处理预算；请降低转图DPI')
    expected = height * (1 + width * channels)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(b''.join(packed), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError('PNG像素长度不匹配或解压超过预算')
    return header, extras, raw


def unfilter_row(filtered, previous, channels, method):
    if method == 0:
        return bytearray(filtered)
    if method == 1:
        row = bytearray(len(filtered))
        for channel in range(channels):
            row[channel::channels] = bytes(value & 255 for value in itertools.accumulate(filtered[channel::channels]))
        return row
    if method == 2:
        return bytearray((a + b) & 255 for a, b in zip(filtered, previous))
    if method not in (3, 4):
        raise ValueError('PNG过滤方式无效')
    row = bytearray(filtered)
    for i in range(len(row)):
        left = row[i - channels] if i >= channels else 0
        up = previous[i]
        upper_left = previous[i - channels] if i >= channels else 0
        if method == 3:
            predictor = (left + up) // 2
        else:
            p = left + up - upper_left
            a, b, c = abs(p - left), abs(p - up), abs(p - upper_left)
            predictor = left if a <= b and a <= c else up if b <= c else upper_left
        row[i] = (row[i] + predictor) & 255
    return row


def crop_png(encoded, rect):
    header, extras, raw = read_png(encoded)
    width, height, depth, color, _, _, _ = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    if not isinstance(rect, list) or len(rect) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect):
        raise ValueError('裁切坐标无效')
    a, b, c, d = rect
    if not (0 <= a < c <= 1000 and 0 <= b < d <= 1000):
        raise ValueError('裁切坐标越界')
    x0, y0 = int(width * a / 1000), int(height * b / 1000)
    x1, y1 = min(width, math.ceil(width * c / 1000)), min(height, math.ceil(height * d / 1000))
    stride = width * channels
    previous = bytearray(stride); output = bytearray(); prior_crop = bytearray((x1 - x0) * channels); nonwhite=0
    for y in range(y1):
        offset = y * (stride + 1)
        row = unfilter_row(raw[offset + 1:offset + stride + 1], previous, channels, raw[offset])
        previous = row
        if y < y0:
            continue
        cropped = row[x0 * channels:x1 * channels]
        if color in (0,2):nonwhite += sum(1 for _ in re.finditer(rb'[^\xff]',cropped))
        # Up filtering preserves every pixel and usually compresses line drawings well.
        output.append(2)
        output.extend((a - b) & 255 for a, b in zip(cropped, prior_crop))
        prior_crop = cropped
    out_header = struct.pack('>IIBBBBB', x1 - x0, y1 - y0, depth, color, 0, 0, 0)
    blob = b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', out_header)
    blob += b''.join(png_chunk(kind, data) for kind, data in extras)
    blob += png_chunk(b'IDAT', zlib.compress(bytes(output), 6)) + png_chunk(b'IEND', b'')
    actual = [1000 * x0 / width, 1000 * y0 / height, 1000 * x1 / width, 1000 * y1 / height]
    return blob, {'original_pixels': [width, height], 'crop_pixels': [x0, y0, x1, y1], 'actual_rect': actual,
                  'pixel_colors_preserved': True, 'crop_method': 'lossless_png_crop',
                  'verified_blank': nonwhite==0 if color in (0,2) else False}


def ink_bounds(encoded):
    header,extras,raw=read_png(encoded)
    width,height,depth,color,_,_,_=header
    if color not in (0,2):return [0,0,1000,1000]
    channels=1 if color==0 else 3;stride=width*channels;previous=bytearray(stride)
    left,top,right,bottom=width,height,-1,-1
    for y in range(height):
        offset=y*(stride+1);row=unfilter_row(raw[offset+1:offset+stride+1],previous,channels,raw[offset]);previous=row
        first=None;last=None
        for match in re.finditer(rb'[^\xff]',row):
            if first is None:first=match.start()
            last=match.start()
        if first is not None:
            left=min(left,first//channels);right=max(right,last//channels);top=min(top,y);bottom=y
    if right<0:return [0,0,1000,1000]
    pad=12
    return [max(0,left-pad)*1000/width,max(0,top-pad)*1000/height,
            min(width,right+pad+1)*1000/width,min(height,bottom+pad+1)*1000/height]


def crop_node(encoded, job_json):
    job = json.loads(job_json)
    try:
        roi=ink_bounds(encoded);a,b,c,d=job['rect']
        rect=[roi[0]+a*(roi[2]-roi[0])/1000,roi[1]+b*(roi[3]-roi[1])/1000,
              roi[0]+c*(roi[2]-roi[0])/1000,roi[1]+d*(roi[3]-roi[1])/1000]
        blob, audit = crop_png(encoded, rect);audit['content_roi']=roi
        text = 'data:image/png;base64,' + base64.b64encode(blob).decode('ascii')
        if len(text) > 16 * 60000:
            raise ValueError('局部图超过当前96万字符传输预算；未删减图像继续识别')
        out = {'valid': 'YES', 'message': '', 'context_json': json.dumps(dict(job, crop_audit=audit), ensure_ascii=False),
               'filename': job['job_id'] + '.png'}
        out.update({'part%d' % i: text[i * 60000:(i + 1) * 60000] for i in range(16)})
        return out
    except (ValueError, TypeError, KeyError, struct.error, zlib.error) as exc:
        out = {'valid': 'NO', 'message': str(exc), 'context_json': job_json, 'filename': job.get('job_id', 'failed') + '.png'}
        out.update({'part%d' % i: '' for i in range(16)})
        return out
