"""Prepare uploadable Dify test batches. No API server or model key required."""
import argparse
import json
from pathlib import Path
import shutil

from recognize import recognize,write_reports
from vision import prepare

SCHEMA='pdf-recognition-test/3'


def export_batches(pdf,out,batch_size=4):
    pdf=Path(pdf);out=Path(out)
    if not 1<=batch_size<=4:raise ValueError('每批图片数量应为1至4')
    if out.exists() and any(out.iterdir()):raise ValueError('输出目录已有文件，请换一个新目录，保留此前结果')
    out.mkdir(parents=True,exist_ok=True)
    print('1/4 正在读取PDF原生文字和线条（不调用千问）...',flush=True)
    native=recognize(pdf,ocr_mode='off')
    write_reports(native,pdf,out/'native_result')
    print('2/4 正在生成高清局部图片...',flush=True)
    manifest=prepare(pdf,native,out/'prepared_regions')
    pages={p['page']:p for p in native['pages']}
    for unit in manifest['units']:
        annotations={a['id']:a for a in pages[unit['page']]['annotations']}
        visible={a['id'] for a in unit['context']['native_or_ocr_anchors']}
        for anchor in unit['context']['native_or_ocr_anchors']:
            anchor['literal_meaning']=annotations[anchor['id']]['meaning']
            anchor['literal_kind']=annotations[anchor['id']]['kind']
        unit['context']['native_parameter_links']=[link for link in pages[unit['page']]['parameter_links']
            if link['target_anchor'] in visible and link['parameter_anchor'] in visible]
        unit['image_name']=native['source_id']+'-'+unit['image']
    groups=[];current=[]
    for unit in manifest['units']:
        if current and (len(current)>=batch_size or len(json.dumps(current,ensure_ascii=False))+len(json.dumps(unit,ensure_ascii=False))>55000):
            groups.append(current);current=[]
        if len(json.dumps(unit,ensure_ascii=False))>55000:raise ValueError('单个分区说明过长，需要进一步细分；未截断原文')
        current.append(unit)
    if current:groups.append(current)
    print('3/4 正在整理Dify上传材料...',flush=True)
    for index,units in enumerate(groups,1):
        dest=out/f'batch_{index:03d}';dest.mkdir()
        packet={'schema_version':SCHEMA,'source_id':native['source_id'],'source_filename':pdf.name,
                'source_summary':native['summary'],'native_extraction_issues':native['issues'],
                'batch_index':index,'batch_count':len(groups),'total_units':len(manifest['units']),
                'units':units,'scope':'原生解析与局部图片，仅作为视觉识图输入；未进行模型验收或工程批准'}
        text=json.dumps(packet,ensure_ascii=False,indent=2)
        if len(text)>110000:raise ValueError('本批说明超过预算，请降低--batch-size；未丢弃分区')
        (dest/'evidence.txt').write_text(text,encoding='utf-8')
        for unit in units:shutil.copyfile(out/'prepared_regions'/unit['image'],dest/unit['image_name'])
    (out/'test_index.json').write_text(json.dumps({'source_id':native['source_id'],'batch_count':len(groups),
        'total_units':len(manifest['units']),'model_calls':0,'batch_folders':[f'batch_{i:03d}' for i in range(1,len(groups)+1)]},indent=2))
    print(f'4/4 准备完成：{len(manifest["units"])}个分区，共{len(groups)}批。',flush=True)
    print('在Dify上传每批的evidence.txt和同批全部PNG；不用上传ZIP。',flush=True)
    print('结果目录：',out.resolve(),flush=True)
    return {'native':native,'batches':groups}


def main():
    p=argparse.ArgumentParser(description='生成本机解析+Dify千问识图测试材料')
    p.add_argument('pdf');p.add_argument('--out',required=True);p.add_argument('--batch-size',type=int,default=4)
    a=p.parse_args()
    try:export_batches(a.pdf,a.out,a.batch_size)
    except (ValueError,RuntimeError) as exc:raise SystemExit(str(exc)) from None


if __name__=='__main__':main()
