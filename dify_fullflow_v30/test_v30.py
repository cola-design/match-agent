"""Generic regression: identity, numbering, transformer omission, partial reads, graph wiring."""
import base64
import json
from pathlib import Path
import unittest
import zlib
import yaml
from reconciliation import *


def manifest(text=''):
    return base64.b64encode(zlib.compress(j({'page_count':1,'native_text':text,
        'uploaded_names':['p.png'],'images':[{'filename':'p.png','page_number':1}]}).encode())).decode()


def context(name='R', mode='reconcile'):
    return {'mode':mode,'view_id':name,'job_id':'P1-'+name,'page':1,'file_serial':1,
            'rect':[0,0,1000,1000],'image_name':'p.png'}


def packet(objects, anchors=None, links=None, name='R', mode='reconcile',complete=True):
    return parse_view(j({'readable':True,'view_complete':complete,'objects':objects,'anchors':anchors or [],
        'links':links or [],'drawings':[{'id':'D1','bbox':[0,0,1000,1000],'cabinet_tags':['AP-TEST']}]}),j(context(name,mode)))['packet_json']


def device(i,raw='MCB-C20A/1P', y=100,category='MCB'):
    return {'id':i,'raw':raw,'kind':'device','category':category,'bbox':[200,y,350,y+20],'symbol':'component'}


def collect(local,master,text=''):
    return join_result(collect_reconciled(local,[master],manifest(text)))


class Regression(unittest.TestCase):
    def test_multiple_local_views_do_not_multiply_master_entities(self):
        local=[packet([device('x')],name=v,mode='detail') for v in ('H1','H2','V2')]
        result=collect(local,packet([device('a'),device('b',y=200)]))
        self.assertEqual(len(result['items']),2)
        self.assertEqual(len(result['quarantined_observations']),3)
        self.assertFalse(result['summary']['local_observations_are_procurement_entities'])

    def test_unmatched_local_observation_is_preserved_but_not_a_product(self):
        result=collect([packet([device('x','OTHER-991',category='unknown')],name='H1',mode='detail')],packet([device('a')]))
        self.assertEqual(len(result['items']),1)
        self.assertIn('local_only_unverified',{e['quarantine_reason'] for e in result['quarantined_observations']})
        self.assertIn('local_reading_unresolved',{e['code'] for e in result['issues']})

    def test_truncated_labels_and_ports_cannot_be_purchase_entities(self):
        objects=[device(str(i),raw) for i,raw in enumerate(['MCB-C','RCB/O','L1MCB-C1','I/O','UTP/CAT'])]
        result=collect([],packet(objects+[device('valid')]))
        self.assertEqual([x['raw'] for x in result['items']],['MCB-C20A/1P'])
        self.assertEqual(len(result['quarantined_observations']),5)

    def test_voltage_transformer_is_separate_from_neighbor_breaker(self):
        objects=[device('b'),device('t','110/24V',y=180,category='transformer'),
                 dict(device('va','630VA',y=200),kind='parameter',category='unknown')]
        p=packet(objects,links=[{'type':'parameter','source':'va','target':'t','trace_visible':True}])
        result=collect([],p,'110/24V\n630VA\nMCB-C20A/1P')
        t=next(x for x in result['items'] if x['category']=='transformer')
        self.assertEqual(t['literal_parameters']['voltage_pair_v'],[110.0,24.0])
        self.assertEqual(t['associated_parameters']['apparent_power_va'],630)
        self.assertEqual(len(result['items']),2)

    def test_combined_voltage_and_breaker_text_is_not_one_device(self):
        result=collect([],packet([device('b','110/24V MCB-C20A/1P')]),'110/24V\nMCB-C20A/1P')
        self.assertEqual(result['items'],[])
        self.assertIn('transformer_symbol_review',{x['code'] for x in result['issues']})

    def test_wire_and_cabinet_are_not_circuit_numbers(self):
        self.assertFalse(acceptable_anchor('WDZB-BYJ-3'))
        self.assertFalse(acceptable_anchor('21APDT2'))
        self.assertFalse(acceptable_anchor('AP-TEST',['AP-TEST']))
        self.assertTrue(acceptable_anchor('3#'))
        self.assertTrue(acceptable_anchor('WP3'))

    def test_printed_numbers_override_local_order(self):
        anchors=[{'id':'a','tag':'7#','bbox':[800,100,820,120],'phase':'L2','load':'Load A','load_bbox':[830,100,930,120],'direction':'horizontal'}]
        links=[{'type':'member','source':'x','target':'a','trace_visible':True}]
        result=collect([packet([device('x')],anchors=[dict(anchors[0],tag='2#')],links=links,name='H1',mode='detail')],packet([device('x')],anchors,links))
        self.assertEqual(result['items'][0]['circuit'],'7#')

    def test_same_tag_different_positions_is_not_silently_bound(self):
        a=[{'id':'a','tag':'3#','bbox':[800,100,820,120],'direction':'horizontal'},
           {'id':'b','tag':'3#','bbox':[800,200,820,220],'direction':'horizontal'}]
        result=collect([],packet([device('x')],a,[{'type':'member','source':'x','target':'a','trace_visible':True}]))
        self.assertIsNone(result['items'][0]['circuit'])

    def test_phase_without_space_retains_rating_and_curve(self):
        p=decode('L1MCB-C16A/1P')['explicit_parameters']
        self.assertEqual((p['rated_current_a'],p['trip_curve'],p['pole_marking']),(16,'C','1P'))

    def test_source_text_extra_instances_produce_explicit_discrepancy(self):
        result=collect([],packet([device('a'),device('b',y=220)]),'MCB-C20A/1P')
        self.assertIn('native_count_discrepancy',{x['code'] for x in result['issues']})

    def test_incomplete_master_is_visible_to_business_gate(self):
        result=collect([],packet([device('a')],complete=False))
        self.assertEqual(result['summary']['master_pages_incomplete'],[1])

    def test_reconcile_hint_does_not_supply_fake_coordinates(self):
        p=packet([device('x')],name='H1',mode='detail')
        job=json.loads(prepare_reconciliation([p],manifest())['jobs'][0])
        request=json.loads(request_view(j(job))['request_json'])
        self.assertEqual(request['mode'],'reconcile')
        self.assertNotIn('bbox',j(request['unverified_local_readings']))

    def test_graph_integrity_and_installed_plugins(self):
        d=yaml.safe_load((Path(__file__).parent/'cabinet_project_v30.yml').read_text())
        nodes=d['workflow']['graph']['nodes'];ids={n['id'] for n in nodes}
        self.assertEqual(len(ids),len(nodes));self.assertEqual(d['dependencies'],[])
        for n in nodes:
            if n['data']['type']=='code':compile(n['data']['code'],n['id'],'exec')
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in d['workflow']['graph']['edges']))
        self.assertEqual(len(d['workflow']['features']['suggested_questions']),6)

    def test_incomplete_master_cannot_overwrite_project_or_make_form(self):
        d=yaml.safe_load((Path(__file__).parent/'cabinet_project_v30.yml').read_text())
        node=next(n for n in d['workflow']['graph']['nodes'] if n['data']['title']=='把识图结果转为待人工核对的项目清单')
        space={};exec(node['data']['code'],space)
        value=space['main']('existing-state-must-not-change',j({'summary':{'master_pages_incomplete':[1]}}),[])
        self.assertEqual(value['valid'],'NO')
        self.assertEqual(value['state_json'],'existing-state-must-not-change')
        self.assertNotIn('file_base64',value)

if __name__=='__main__':unittest.main(verbosity=2)
