"""Retain a prior result without inventing missing position evidence.

This is a failure-policy replay, not a new image recognition result.
"""
import json
import os
from pathlib import Path
from spatial import parse_view, collect_spatial, prepare_jobs

ROOT=Path(__file__).resolve().parent


def main():
    received=json.loads(Path(os.environ['PRIVATE_LEGACY_RESULT']).read_text())
    old=json.loads(received['result_json'])
    objects=[]
    for index,item in enumerate(old['items'],1):
        objects.append({'id':'O%d'%index,'raw':item['raw'],'category':item['category'],
                        'kind':'cable' if item['category']=='cable' else 'unknown' if item['category']=='unknown' else 'device',
                        'bbox':None,'symbol':None})
    data={'readable':True,'view_complete':False,'drawings':[],'objects':objects,'anchors':[],'links':[],
          'unresolved':['Legacy result did not contain coordinate evidence; no positions reconstructed.']}
    job={'job_id':'P1-H2','page':1,'image_name':'fixture_page_1.png','view_id':'H2','mode':'detail','rect':[0,0,1000,1000]}
    packet=parse_view(json.dumps(data,ensure_ascii=False),json.dumps(job))['packet_json']
    metadata={'success':True,'total_pages':1,'total_images':1,'images':[
        {'filename':job['image_name'],'file_index':1,'page_number':1}]}
    prepared=prepare_jobs([metadata],[{'filename':job['image_name']}],[received['source_text']],'')
    out=collect_spatial([packet],prepared['manifest_compact']);new=json.loads(out['result_json'])
    assert all(x['circuit_id'] is None for x in new['items'])
    assert new['summary']['selection_approved'] is False and new['summary']['pending_checks']>0
    assert len(new['items'])+len(new['document_annotations'])==len(old['items'])
    assert all(x['bbox'] is None for x in new['items'])
    report={'original_candidate_rows':len(old['items']),'retained_rows_and_annotations':len(new['items'])+len(new['document_annotations']),
            'new_product_candidates':len(new['items']),'separated_annotations':len(new['document_annotations']),
            'fabricated_positions':0,'bindings_approved_without_positions':0,'actual_model_calls':0,
            'scope':'Replayed supplied V6.1 labels with their actual missing coordinates. No new model output or recovery of omitted physical instances.'}
    (ROOT/'validation_private_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
