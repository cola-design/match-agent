"""Generic PDF/recognition regression, with no customer-specific drawing rules."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import unittest
import zlib
import yaml
from pdf_native import *
from native_workflow import *


def make_pdf(rotate=0,content=None,form=False):
    stream=content or b'BT /F1 12 Tf 1 0 0 1 80 160 Tm (MCB-C10A/2P) Tj ET'
    values={1:b'<< /Type /Catalog /Pages 2 0 R >>',2:b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            3:('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Rotate %d /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'%rotate).encode(),
            4:b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
            5:b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream'}
    return b'%PDF-1.5\n'+b''.join(str(k).encode()+b' 0 obj\n'+v+b'\nendobj\n' for k,v in values.items())+b'trailer << /Root 1 0 R >>\n%%EOF'


def parsed(raw):
    out=native_pdf_node(base64.b64encode(raw).decode())
    return out,[uncompress(p) for p in out['native_pages']]


def span(ref,text,box):return {'id':ref,'text':text,'bbox':box,'baseline':[box[0],box[3]-2]}


def encoded_manifest(spans=None,segments=None):
    page={'page':1,'spans':spans or [],'straight_segments':segments or [],'status':'available','warnings':[]}
    return base64.b64encode(zlib.compress(j({'page_count':1,'native_text':'\n'.join(s['text'] for s in page['spans']),
        'native_pages':{'1':page},'native_status':'available','images':[{'filename':'p.png','page_number':1}],
        'uploaded_names':['p.png']}).encode())).decode()


def context(native,job='H1',mode='detail'):
    return native_context(j({'job_id':'P1-'+job,'page':1,'view_id':job,'mode':mode,'rect':[0,0,1000,1000],
                            'image_name':'p.png','file_serial':1}),native)['context_json']


def packet(objects,ctx,drawings=None):
    return native_parse(j({'readable':True,'view_complete':True,'objects':objects,'anchors':[],'links':[],
                           'drawings':drawings or []}),ctx)['packet_json']


def result(value):
    d=json.loads(value['result_json'])
    if not d.get('details_in'):return d
    out={'summary':d['summary']}
    for p in value['result_pages']:
        for k,v in json.loads(p).items():
            if isinstance(v,list):out.setdefault(k,[]).extend(v)
    return out


def object_for(s,i='X',category='MCB'):
    return {'id':i,'raw':s['text'],'native_refs':[s['id']],'kind':'device','category':category,'bbox':[10,10,20,20],'symbol':'visible component'}


class PDFTests(unittest.TestCase):
    def test_simple_text_preserves_position_and_spelling(self):
        r,p=parsed(make_pdf());self.assertEqual(r['native_status'],'available')
        self.assertEqual(p[0]['spans'][0]['text'],'MCB-C10A/2P')
        self.assertEqual(p[0]['spans'][0]['baseline'],[200,1000*140/300])

    def test_all_page_rotations(self):
        expected={0:[200,1000*140/300],90:[1000*160/300,200],180:[800,1000*160/300],270:[1000*140/300,800]}
        for angle in expected:
            _,p=parsed(make_pdf(angle));self.assertEqual(p[0]['spans'][0]['baseline'],expected[angle])

    def test_string_escape_hex_and_tj_adjustment(self):
        r,p=parsed(make_pdf(content=b'BT /F1 10 Tf 80 100 Td [(A\\050B\\051) -100 <4344>] TJ ET'))
        self.assertEqual([s['text'] for s in p[0]['spans']],['A(B)','CD'])
        self.assertGreater(p[0]['spans'][1]['baseline'][0],p[0]['spans'][0]['baseline'][0])

    def test_unicode_cmap_ranges_and_arrays(self):
        data=b'2 beginbfchar <0001><4E2D> <0002><6587> endbfchar 2 beginbfrange <0003><0004><0041> <0005><0006>[<0030><0031>] endbfrange'
        m=pdf_cmap(data);self.assertEqual(''.join(m[i.to_bytes(2,'big')] for i in range(1,7)),'中文AB01')

    def test_compressed_object_stream_and_content(self):
        raw=zlib.compress(b'7 0 << /Type /Example /Value 42 >>')
        source=b'%PDF-1.5\n6 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Filter /FlateDecode /Length '+str(len(raw)).encode()+b' >>\nstream\n'+raw+b'\nendstream\nendobj'
        o=pdf_objects(source);self.assertEqual(o[7]['value']['/Value'],42)

    def test_graphics_transform_and_q_restore(self):
        _,p=parsed(make_pdf(content=b'q 2 0 0 2 10 20 cm BT /F1 10 Tf 10 20 Td (X) Tj ET Q BT /F1 10 Tf 10 20 Td (Y) Tj ET'))
        self.assertEqual(p[0]['spans'][0]['baseline'],[75,800])
        self.assertEqual(p[0]['spans'][1]['baseline'],[25,1000*280/300])

    def test_no_native_text_is_reported_as_such(self):
        r,p=parsed(make_pdf(content=b'10 10 m 200 10 l S'))
        self.assertEqual(r['native_status'],'unavailable');self.assertEqual(p[0]['spans'],[])

    def test_unsupported_encoding_does_not_fabricate_text(self):
        raw=make_pdf().replace(b'/WinAnsiEncoding',b'/UnknownEncoding')
        r,p=parsed(raw);self.assertEqual(r['native_status'],'unavailable');self.assertEqual(p[0]['spans'],[])

    def test_invalid_input_is_non_destructive_fallback(self):
        for text in ('not base64','YWJj',''):
            self.assertEqual(native_pdf_node(text)['native_pages'],[])

    def test_decompression_bomb_is_rejected(self):
        raw=zlib.compress(b'A'*12000001)
        with self.assertRaisesRegex(ValueError,'budget'):pdf_stream({}, {'value':{'/Filter':'/FlateDecode'},'stream':raw})

    def test_form_xobject_text_matrix(self):
        stream=b'BT /F1 10 Tf 10 20 Td (FORM) Tj ET'
        raw=make_pdf(content=b'q 1 0 0 1 20 30 cm /Fm Do Q')
        raw=raw.replace(b'/Resources << /Font << /F1 4 0 R >> >>',b'/Resources << /Font << /F1 4 0 R >> /XObject << /Fm 6 0 R >> >>')
        raw+=b'\n6 0 obj\n<< /Subtype /Form /Matrix [1 0 0 1 5 5] /Resources << /Font << /F1 4 0 R >> >> /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream\nendobj'
        r,p=parsed(raw);self.assertEqual(p[0]['spans'][0]['text'],'FORM')
        self.assertEqual(p[0]['spans'][0]['baseline'],[87.5,1000*245/300])

    def test_multiple_pages_have_independent_native_ids(self):
        raw=make_pdf().replace(b'/Kids [3 0 R] /Count 1',b'/Kids [3 0 R 6 0 R] /Count 2')
        raw+=b'\n6 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Rotate 90 /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj'
        r,p=parsed(raw);self.assertEqual(len(p),2)
        self.assertNotEqual(p[0]['spans'][0]['id'],p[1]['spans'][0]['id'])


class RecognitionTests(unittest.TestCase):
    def test_timer_above_another_branch_cannot_be_assigned_by_nearest_row(self):
        item={'raw':'时间控制器','native_ref':'P1-T1','page':1,'drawing_id':'D1','bbox':[200,101,250,119]}
        anchor={'page':1,'drawing_id':'D1','direction':'horizontal','wire_segment':[200,110,800,110],'id':'C1'}
        self.assertIsNone(native_branch_candidate(item,[anchor],{'straight_segments':[]}))

    def test_wire_end_load_alignment_does_not_shift_to_next_number(self):
        spans=[span('P1-T1','5#',[790,90,800,100]),span('P1-T2','6#',[790,130,800,140]),
               span('P1-T3','Load Alpha',[810,105,880,117]),span('P1-T4','Load Beta',[810,141,880,153])]
        page={'page':1,'spans':spans,'straight_segments':[[200,102,802,102],[200,142,802,142]]}
        anchors=native_circuit_geometry(page,[{'id':'D1','page':1,'bbox':[0,0,1000,1000]}])
        self.assertEqual({a['tag']:a['load'] for a in anchors},{'5#':'Load Alpha','6#':'Load Beta'})

    def test_vertical_layout_load_alignment(self):
        spans=[span('P1-T1','1#',[90,790,100,800]),span('P1-T2','2#',[130,790,140,800]),
               span('P1-T3','Load A',[105,810,117,880]),span('P1-T4','Load B',[141,810,153,880])]
        page={'page':1,'spans':spans,'straight_segments':[[102,200,102,802],[142,200,142,802]]}
        anchors=native_circuit_geometry(page,[{'id':'D1','page':1,'bbox':[0,0,1000,1000]}])
        self.assertEqual({a['tag']:a['load'] for a in anchors},{'1#':'Load A','2#':'Load B'})

    def test_left_hand_load_labels_are_not_forced_to_right(self):
        spans=[span('P1-T1','1#',[195,90,205,100]),span('P1-T2','2#',[195,130,205,140]),
               span('P1-T3','Load Left A',[100,103,185,115]),span('P1-T4','Load Left B',[100,143,185,155])]
        page={'page':1,'spans':spans,'straight_segments':[[198,102,800,102],[198,142,800,142]]}
        anchors=native_circuit_geometry(page,[{'id':'D1','page':1,'bbox':[0,0,1000,1000]}])
        self.assertEqual({a['tag']:a['load'] for a in anchors},{'1#':'Load Left A','2#':'Load Left B'})

    def test_printed_circuit_remains_visible_when_wire_geometry_is_unavailable(self):
        page={'page':1,'spans':[span('P1-T1','8#',[100,100,110,120])],'straight_segments':[]}
        anchors=native_circuit_geometry(page,[{'id':'D1','page':1,'bbox':[0,0,1000,1000]}])
        self.assertEqual([a['tag'] for a in anchors],['8#'])
        self.assertIsNone(anchors[0]['load']);self.assertFalse(anchors[0]['load_position_checked'])

    def test_visual_and_native_rating_disagreement_stays_visible(self):
        a=span('P1-T1','MCB-C10A/2P',[100,100,160,120]);m=encoded_manifest([a]);obj=object_for(a);obj['raw']='MCB-C20A/2P'
        p=packet([obj],context(m));d=result(collect_native([p],[],m))
        self.assertEqual(d['items'][0]['raw'],'MCB-C10A/2P')
        self.assertIn('native_visual_text_conflict',{q['code'] for q in d['issues']})
    def test_same_native_identity_merges_across_views(self):
        s=span('P1-T1','MCB-C10A/2P',[100,100,160,120]);m=encoded_manifest([s])
        p1=json.loads(packet([object_for(s)],context(m,'H1')));p2=json.loads(packet([object_for(s)],context(m,'V2')))
        groups,_=merge_positions(p1['observations']+p2['observations'],'O')
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['bbox'],s['bbox'])

    def test_identical_models_at_different_native_positions_remain_distinct(self):
        a=span('P1-T1','MCB-C10A/2P',[100,100,160,120]);b=span('P1-T2',a['text'],[100,101,160,121]);m=encoded_manifest([a,b])
        p=json.loads(packet([object_for(a,'A'),object_for(b,'B')],context(m)))
        self.assertEqual(len(merge_positions(p['observations'],'O')[0]),2)

    def test_duplicate_same_native_id_in_one_response_does_not_multiply(self):
        a=span('P1-T1','MCB-C10A/2P',[100,100,160,120]);m=encoded_manifest([a])
        p=json.loads(packet([object_for(a,'A'),object_for(a,'B')],context(m)))
        self.assertEqual(len(merge_positions(p['observations'],'O')[0]),1)

    def test_combined_transformer_and_breaker_go_to_focused_repair(self):
        a=span('P1-T1','110/24V',[100,100,160,120]);b=span('P1-T2','MCB-C10A/2P',[170,100,240,120]);m=encoded_manifest([a,b])
        obj=object_for(a);obj['native_refs']=[a['id'],b['id']];obj['raw']='110/24V MCB-C10A/2P'
        p=packet([obj],context(m));jobs=[json.loads(x) for x in native_repairs([p],m)['jobs']]
        self.assertEqual({j['focus_native_ids'][0] for j in jobs},{a['id'],b['id']})

    def test_ratio_without_visible_transformer_is_not_a_device(self):
        a=span('P1-T1','110/24V',[100,100,160,120]);m=encoded_manifest([a]);obj=object_for(a,category='unknown');obj['symbol']=None
        p=json.loads(packet([obj],context(m)));self.assertFalse(any(o['kind']=='device' for o in p['observations']))

    def test_missing_native_label_remains_visible_without_invented_symbol(self):
        a=span('P1-T1','MCB-C10A/2P',[100,100,160,120]);m=encoded_manifest([a]);p=packet([],context(m,'overview','overview'),[{'id':'D1','bbox':[0,0,1000,1000],'cabinet_tags':['AP1']}])
        d=result(collect_native([p],[],m))
        self.assertEqual(len(d['items']),1);self.assertFalse(d['items'][0]['symbol_observed']);self.assertEqual(d['items'][0]['category'],'unknown')

    def test_cabinet_ids_and_quantities_are_separate(self):
        a=span('P1-T1','12AP1',[100,100,160,120]);b=span('P1-T2','12BPDT3',[170,100,240,120]);m=encoded_manifest([a,b])
        p=packet([],context(m,'overview','overview'),[{'id':'D1','bbox':[0,0,1000,1000],'cabinet_tags':['电气柜']}])
        d=result(collect_native([p],[],m));self.assertEqual(set(d['drawings'][0]['cabinet_tags']),{'12AP1','12BPDT3'})
        self.assertNotIn('quantity',d['drawings'][0])

    def test_only_current_crop_native_text_is_sent_to_model(self):
        m=encoded_manifest([span('P1-T1','MCB-C10A/2P',[100,100,160,120]),span('P1-T2','FAR-MODEL-999',[800,800,900,820])])
        ctx=json.loads(context(m));ctx['rect']=[0,0,300,300]
        request=native_request(j(ctx))['request_json'];self.assertNotIn('FAR-MODEL',request)

    def test_chunked_result_keeps_empty_circuits_and_all_notes(self):
        m=uncompress(encoded_manifest());m['page_count']=2;m['native_pages']={}
        encoded=base64.b64encode(zlib.compress(j(m).encode())).decode()
        packets=[]
        for number in (1,2):
            ctx={'mode':'overview','view_id':'overview','job_id':'P%d-overview'%number,'page':number,'file_serial':1,'rect':[0,0,1000,1000],'image_name':'p.png'}
            objects=[{'id':str(i),'raw':str(i)+' '+'annotation '*14,'kind':'note','category':'unknown','bbox':[10+i,10,30+i,30]} for i in range(64)]
            packets.append(parse_view(j({'readable':True,'view_complete':True,'objects':objects,'anchors':[],'links':[],'drawings':[]}),j(ctx))['packet_json'])
        value=collect_native(packets,[],encoded);d=result(value)
        self.assertGreater(len(value['result_pages']),1)
        self.assertEqual(len(d['document_annotations']),128)
        self.assertEqual(d.get('circuits',[]),[])

    def test_native_bus_prevents_incoming_device_from_being_assigned_to_outgoing_row(self):
        anchors=[{'id':'C'+str(i),'page':1,'drawing_id':'D1','direction':'horizontal','wire_segment':[300,100+i*40,800,100+i*40]} for i in range(3)]
        page={'straight_segments':[[250,80,250,200]]}
        incoming={'raw':'MCCB100N-MA40A/3P','native_ref':'P1-T1','page':1,'drawing_id':'D1','bbox':[100,90,180,105]}
        outgoing=dict(incoming,bbox=[280,90,340,105])
        self.assertIsNone(native_branch_candidate(incoming,anchors,page))
        self.assertEqual(native_branch_candidate(outgoing,anchors,page)['id'],'C0')

    def test_ports_and_clipped_readings_cannot_reenter_procurement_list(self):
        seed=span('P1-T1','MCB-C10A/2P',[100,100,160,120]);m=encoded_manifest([seed])
        objects=[{'id':str(i),'raw':r,'kind':'device','category':'unknown','bbox':[250+i*30,100,270+i*30,120],'symbol':'uncertain'} for i,r in enumerate(['I/O','MCB-C','RCB/O'])]
        p=packet(objects,context(m));d=result(collect_native([p],[],m))
        self.assertEqual([x['raw'] for x in d['items']],['MCB-C10A/2P'])
        self.assertFalse(d['items'][0]['symbol_observed'])

    def test_readonly_parser_has_no_network_or_shell_calls(self):
        text=(Path(__file__).parent/'pdf_native.py').read_text()
        for forbidden in ('import requests','import urllib','import subprocess','import socket','os.system','eval('):self.assertNotIn(forbidden,text)

    def test_yml_graph_and_plugin_dependencies(self):
        d=yaml.safe_load((Path(__file__).parent/'cabinet_project_v34.yml').read_text());nodes=d['workflow']['graph']['nodes'];ids={n['id'] for n in nodes}
        self.assertEqual(len(ids),len(nodes));self.assertEqual(d['dependencies'],[])
        for n in nodes:
            if n['data']['type']=='code':compile(n['data']['code'],n['id'],'exec')
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in d['workflow']['graph']['edges']))

if __name__=='__main__':unittest.main(verbosity=2)
