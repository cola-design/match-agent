import base64
from copy import deepcopy
import json
from pathlib import Path
import struct
import unittest
import zlib
from png_crop import png_chunk, crop_png, crop_node
from spatial import (bbox, map_box, parse_view, collect_spatial, prepare_jobs,
                     job_context, check_view_files, crop_failure, kind_of)

ROOT = Path(__file__).resolve().parent


def manifest():
    return prepare_jobs([{'success':True,'total_pages':1,'total_images':1,'images':[
        {'filename':'fixture_page_1.png','file_index':1,'page_number':1}]}],
        [{'filename':'fixture_page_1.png'}],[''], '')


def context(view='H2', rect=None):
    return {'job_id':'P1-'+view,'view_id':view,'page':1,'file_serial':1,'image_name':'fixture_page_1.png',
            'mode':'overview' if view=='overview' else 'detail','rect':rect or [0,0,1000,1000]}


def response(objects=None, anchors=None, links=None, overview=False):
    return {'readable':True,'view_complete':True,'drawings':[
        {'id':'D1','title':'Synthetic fixture','cabinet_tags':['BOX-X'],'bbox':[0,0,1000,1000]}] if overview else [],
        'objects':objects or [],'anchors':anchors or [],'links':links or [],'unresolved':[]}


def obj(identity, raw, rect, kind='device', category='MCB'):
    return {'id':identity,'raw':raw,'bbox':rect,'kind':kind,'category':category}


def anchor(identity, tag, y=210, direction='horizontal'):
    return {'id':identity,'tag':tag,'bbox':[880,y-10,920,y+10], 'phase':'L1','load':'Fixture load','direction':direction}


def link(source, target, kind='member', visible=True):
    return {'type':kind,'source':source,'target':target,'trace_visible':visible}


def parse(data, view='H2'):
    return parse_view(json.dumps(data,ensure_ascii=False),json.dumps(context(view)))['packet_json']


def collect(data, others=None):
    packets=[parse(response(overview=True),'overview'),parse(data)]
    packets.extend(others or [])
    packets.extend(parse(response(),v) for v in ('H1','H3','V1','V2','V3') if 'P1-'+v not in ''.join(packets))
    out=collect_spatial(packets,manifest()['manifest_compact'])
    return json.loads(out['result_json'])


class PositionTests(unittest.TestCase):
    def test_real_mapping_uses_crop_origin(self):
        self.assertEqual(map_box([100,200,300,400],[0,300,1000,700]),[100,380,300,460])

    def test_invalid_position_is_not_repaired(self):
        self.assertIsNone(bbox([0,0,1001,20])); self.assertIsNone(bbox([True,0,10,20]))

    def test_same_model_at_distinct_positions_remains_separate(self):
        data=response(objects=[obj('A','MCCB100N-MA50A/3P',[100,100,300,120]),
                               obj('B','MCCB100N-MA50A/3P',[100,150,300,170]),
                               obj('C','MCCB100N-MA50A/3P',[100,350,300,370])])
        result=collect(data)
        self.assertEqual(len(result['items']),3)

    def test_same_position_in_different_views_merges(self):
        data=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])])
        result=collect(data,[parse(data,'V1')])
        self.assertEqual(len(result['items']),1)
        self.assertEqual(len(result['items'][0]['view_ids']),2)

    def test_missing_positions_keep_rows_and_block_binding(self):
        result=collect(response(objects=[obj('A','MCB-C16A/1P',None)],anchors=[anchor('B','F7')],links=[link('A','B')]))
        self.assertEqual(len(result['items']),1);self.assertIsNone(result['items'][0]['circuit_id'])

    def test_wrong_row_relation_is_rejected(self):
        data=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])],
                      anchors=[anchor('R','F7',210),anchor('W','F12',310)],links=[link('A','W')])
        result=collect(data)
        self.assertIsNone(result['items'][0]['circuit'])
        self.assertIn('binding_geometry_conflict',{x['code'] for x in result['issues']})

    def test_explicit_same_row_can_bind_arbitrary_printed_tag(self):
        data=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])],
                      anchors=[anchor('R','F7',210),anchor('W','F12',310)],links=[link('A','R')])
        result=collect(data)
        self.assertEqual(result['items'][0]['circuit'],'F7')
        self.assertFalse(result['summary']['selection_approved'])

    def test_vertical_row_checks_x_coordinate(self):
        a=anchor('R','Q9');a['bbox']=[200,880,220,920];a['direction']='vertical'
        result=collect(response(objects=[obj('A','MCB-C16A/1P',[200,100,220,230])],anchors=[a],links=[link('A','R')]))
        self.assertEqual(result['items'][0]['circuit'],'Q9')

    def test_no_inference_from_nearby_label_without_traced_link(self):
        result=collect(response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])],anchors=[anchor('R','F7')]))
        self.assertIsNone(result['items'][0]['circuit'])

    def test_conflicting_views_do_not_choose_majority_blindly(self):
        # Same spatial circuit label with incompatible tag values is an explicit conflict.
        first=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])],anchors=[anchor('R','F7')],links=[link('A','R')])
        second=deepcopy(first);second['anchors'][0]['tag']='F12'
        result=collect(first,[parse(second,'V1')])
        self.assertIsNone(result['items'][0]['circuit'])

    def test_same_number_in_other_drawing_does_not_bind(self):
        overview=response(overview=True);overview['drawings']=[
            {'id':'L','title':'Left','cabinet_tags':[],'bbox':[0,0,450,1000]},
            {'id':'R','title':'Right','cabinet_tags':[],'bbox':[550,0,1000,1000]}]
        data=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])],anchors=[anchor('R','1#')],links=[link('A','R')])
        out=collect_spatial([parse(overview,'overview'),parse(data)],manifest()['manifest_compact'])
        result=json.loads(out['result_json']);self.assertIsNone(result['items'][0]['circuit'])

    def test_ambiguous_object_ids_do_not_bind_first_candidate(self):
        data=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220]),obj('A','MCB-C16A/1P',[100,300,240,320])],
                      anchors=[anchor('R','F7')],links=[link('A','R')])
        result=collect(data)
        self.assertEqual(len(result['items']),2)
        self.assertTrue(all(x['circuit_id'] is None for x in result['items']))

    def test_same_position_read_as_mcb_and_rcb_is_visible_conflict(self):
        first=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])])
        second=response(objects=[obj('A','RCB/C16A/1P+N 30mA',[100,200,240,220])])
        result=collect(first,[parse(second,'V1')])
        self.assertIn('object_label_conflict',{x['code'] for x in result['issues']})
        self.assertTrue(all(x['instance_status']=='conflicting_readings' for x in result['items']))


class SeparationTests(unittest.TestCase):
    def test_cabinet_and_note_never_become_products(self):
        data=response(objects=[obj('A','BOX-X',[100,20,180,40],category='unknown'),
                               obj('B','注：保护要求另见说明',[100,500,400,530],category='unknown'),
                               obj('C','MCB-C16A/1P',[100,200,240,220])])
        result=collect(data)
        self.assertEqual(len(result['items']),1)
        self.assertEqual({x['kind'] for x in result['document_annotations']},{'cabinet_tag','note'})

    def test_capacity_is_attached_to_transformer(self):
        data=response(objects=[obj('A','220/36V',[100,200,240,220],category='transformer'),
                               obj('B','1000VA',[100,225,240,245],category='unknown')],links=[link('B','A','parameter')])
        result=collect(data)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['associated_parameters']['apparent_power_va'],1000)

    def test_pc_class_cannot_attach_to_mcb(self):
        data=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220]),obj('B','PC级',[100,170,140,185],category='unknown')],
                      links=[link('B','A','parameter')])
        result=collect(data)
        self.assertIsNone(result['document_annotations'][0]['target_id'])

    def test_io_is_not_outgoing_circuit(self):
        result=collect(response(anchors=[anchor('R','I/O')]))
        self.assertEqual(result['circuits'],[])

    def test_unbound_cable_is_explicit_pending(self):
        result=collect(response(objects=[obj('A','UTP/CAT5',[100,200,240,220],kind='cable',category='cable')]))
        self.assertEqual(result['summary']['unbound_cable_candidates'],1)
        self.assertIn('cable_scope_unconfirmed',{x['code'] for x in result['issues']})

    def test_invalid_json_keeps_failure_diagnostics(self):
        result=json.loads(parse_view('{"objects":[',json.dumps(context()))['packet_json'])
        self.assertEqual(result['status'],'failed');self.assertTrue(result['diagnostics']['raw_excerpt'])

    def test_empty_llm_default_is_failed_not_successful_zero_items(self):
        result=json.loads(parse_view('{}',json.dumps(context()))['packet_json'])
        self.assertEqual(result['status'],'failed')

    def test_identity_check_does_not_use_original_as_crop(self):
        ctx=json.dumps(context())
        self.assertEqual(check_view_files([{'filename':'fixture_page_1.png'}],ctx)['valid'],'NO')
        self.assertEqual(check_view_files([{'filename':'P1-H2.png'}],ctx)['valid'],'YES')

    def test_jobs_cover_all_pages_and_regions(self):
        out=manifest();self.assertEqual(out['valid'],'YES');self.assertEqual(len(out['jobs']),7)
        job=json.loads(out['jobs'][1]);self.assertEqual(job['rect'],[0,0,1000,400])
        c=job_context(out['jobs'][1],out['manifest_compact']);self.assertEqual(c['file_serial'],'1')


class PNGTests(unittest.TestCase):
    def fixture(self):
        # 2x2 RGB raster with no filters.
        raw=b'\x00'+bytes([255,0,0,0,255,0])+b'\x00'+bytes([0,0,255,255,255,255])
        blob=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))
        blob+=png_chunk(b'IDAT',zlib.compress(raw))+png_chunk(b'IEND',b'')
        return 'data:image/png;base64,'+base64.b64encode(blob).decode()

    def test_pixel_crop_dimensions(self):
        blob,audit=crop_png(self.fixture(),[500,0,1000,1000])
        self.assertEqual(audit['crop_pixels'],[1,0,2,2])
        self.assertEqual(struct.unpack('>II',blob[16:24]),(1,2))

    def test_corrupt_png_rejected(self):
        text=self.fixture();blob=bytearray(base64.b64decode(text.split(',')[1]));blob[20]^=1
        with self.assertRaises(ValueError):crop_png(base64.b64encode(blob).decode(),[0,0,1000,1000])

    def test_crop_outputs_are_bounded_and_rejoin_exactly(self):
        out=crop_node(self.fixture(),json.dumps(context()))
        self.assertEqual(out['valid'],'YES')
        text=''.join(out['part%d'%i] for i in range(16))
        self.assertTrue(text.startswith('data:image/png;base64,'))
        self.assertTrue(all(len(out['part%d'%i])<=60000 for i in range(16)))

    def test_standard_line_wrapped_base64_is_supported(self):
        prefix,text=self.fixture().split(',')
        wrapped=prefix+','+'\n'.join(text[i:i+32] for i in range(0,len(text),32))
        a,_=crop_png(wrapped,[0,0,1000,1000]);b,_=crop_png(self.fixture(),[0,0,1000,1000])
        self.assertEqual(a,b)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'actual_model_calls':0,
            'scope':'Synthetic geometry/metadata variations and PNG processing; no live model accuracy claim.'}
    (ROOT/'validation_rules.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(not result.wasSuccessful())
