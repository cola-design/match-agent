import copy
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import ObjectId
from product_contract import normalize_spec, normalize_product, ProductContractError
from repair_products import build_plan, apply_plan, delta


def fixture():
    return {'_id':ObjectId(),'material_code':'00123456','item_id':16277198,
            'description':'example','category':json.dumps({'middle':'小型断路器'}),
            'standard_specs':json.dumps({'rated_voltage':{'value_text':'AC380V','value_num':80,'unit':'V'},
                                        'multi':{'value_text':'AC230/400V','value_num':30}}),
            'source_attributes':'[]','quality_flags':'[]','provenance':'{}',
            'source_status':'有效','data_status':'ACTIVE','publication_basis':'AUTO_RULE_V1'}

class Cursor(list):
    def batch_size(self,n): return self

class Collection:
    def __init__(self,docs):self.docs=copy.deepcopy(docs);self.calls=0
    def find(self,q): return Cursor(copy.deepcopy(self.docs))
    def find_one(self,q):
        for doc in self.docs:
            if all(doc.get(k)==v for k,v in q.items()):return copy.deepcopy(doc)
    def bulk_write(self,ops,ordered=True):
        self.calls+=1; matched=0
        for op in ops:
            for d in self.docs:
                def match(k,v):
                    if isinstance(v,dict) and '$eq' in v:return k in d and d[k]==v['$eq']
                    if isinstance(v,dict) and '$exists' in v:return (k in d)==v['$exists']
                    return d.get(k)==v
                if all(match(k,v) for k,v in op._filter.items()):
                    matched+=1;d.update(copy.deepcopy(op._doc.get('$set',{})))
                    for k in op._doc.get('$unset',{}):d.pop(k,None)
                    break
        return SimpleNamespace(matched_count=matched,modified_count=matched)

class RepairTest(unittest.TestCase):
    def test_scalar_and_compound(self):
        self.assertEqual(normalize_spec({'value_text':'AC380V','value_num':80},'rated_voltage')['value_num'],380)
        self.assertEqual(normalize_spec({'value_text':'DC24V','value_num':4},'control_voltage')['value_num'],24)
        self.assertNotIn('value_num',normalize_spec({'value_text':'AC230/400V','value_num':30},'rated_voltage'))
        self.assertNotIn('value_num',normalize_spec({'value_text':'3P+N','value_num':3},'pole_count'))
        self.assertEqual(normalize_spec({'value_text':'1.5mm²'},'cross_section')['unit'],'mm2')

    def test_normalization_is_idempotent_and_keeps_publication_state(self):
        doc=fixture(); clean=normalize_product(doc)
        self.assertEqual(normalize_product(clean),clean)
        self.assertEqual(clean['material_code'],doc['material_code'])
        self.assertEqual(clean['data_status'],'ACTIVE')
        self.assertIsInstance(doc['category'],str)
        with self.assertRaises(ProductContractError):normalize_product({**doc,'material_code':123})

    def test_preview_apply_verify_and_rollback(self):
        original=fixture();coll=Collection([original])
        with tempfile.TemporaryDirectory() as folder:
            plan,report=build_plan(coll,Path(folder))
            self.assertEqual(coll.calls,0)
            self.assertEqual(report['stats']['documents_to_change'],1)
            self.assertEqual(coll.docs,[original])
            self.assertEqual(apply_plan(coll,plan),1)
            self.assertEqual(delta(coll.docs[0]),{})
            self.assertEqual(coll.docs[0]['data_status'],'ACTIVE')
            self.assertEqual(apply_plan(coll,plan,rollback=True),1)
            self.assertEqual(coll.docs,[original])
            self.assertEqual(apply_plan(coll,plan,rollback=True),0)

    def test_rollback_skips_records_not_yet_applied(self):
        original=fixture();coll=Collection([original])
        with tempfile.TemporaryDirectory() as folder:
            plan,_=build_plan(coll,Path(folder))
            self.assertEqual(apply_plan(coll,plan,rollback=True),0)
            self.assertEqual(coll.docs,[original])

    def test_apply_guard_refuses_to_overwrite_concurrent_edits(self):
        coll=Collection([fixture()])
        with tempfile.TemporaryDirectory() as folder:
            plan,_=build_plan(coll,Path(folder));coll.docs[0]['category']='manually changed'
            with self.assertRaises(RuntimeError):apply_plan(coll,plan)
            self.assertEqual(coll.docs[0]['category'],'manually changed')

if __name__=='__main__': unittest.main()
