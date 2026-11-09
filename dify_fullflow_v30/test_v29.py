import base64,json,unittest
from copy import deepcopy
from io import BytesIO
import struct,zlib
from spatial import *
from png_crop import *

def png(w=60,h=50,ink=None):
 rows=[]
 for y in range(h):
  row=bytearray([255]*(w*3))
  for x in range(w):
   if ink and ink[0]<=x<ink[2] and ink[1]<=y<ink[3]:row[x*3:x*3+3]=bytes([0,180,0])
  rows.append(b'\0'+row)
 return b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+png_chunk(b'IDAT',zlib.compress(b''.join(rows)))+png_chunk(b'IEND',b'')
def ctx(mode='detail'):
 return {'mode':mode,'view_id':'overview' if mode=='overview' else 'H2','job_id':'P1-overview' if mode=='overview' else 'P1-H2','page':1,'file_serial':1,'rect':[0,0,1000,1000],'image_name':'p.png'}
def raw(objects):return {'readable':True,'view_complete':True,'objects':objects,'drawings':[],'anchors':[],'links':[]}
class Tests(unittest.TestCase):
 def test_parameter_geometry_does_not_guess_distant_targets(self):
  self.assertTrue(parameter_geometry_compatible({'bbox':[100,100,150,120]},{'bbox':[120,120,180,140]}))
  self.assertFalse(parameter_geometry_compatible({'bbox':[100,100,150,120]},{'bbox':[700,700,800,800]}))
  self.assertFalse(parameter_geometry_compatible({'bbox':None},{'bbox':[120,120,180,140]}))
 def test_parameter_type_conflict_requires_review(self):
  self.assertTrue(parameter_type_conflict({'residual_current_ma':30},{'literal_parameters':{'drawing_abbreviation':'MCB'}}))
  self.assertTrue(parameter_type_conflict({'apparent_power_va':1000},{'category':'MCB'}))
  self.assertEqual(parameter_type_conflict({'apparent_power_va':1000},{'category':'transformer'}),'')
 def test_overview_inventory_is_quarantined(self):
  x=parse_view(json.dumps(raw([{'id':'X','raw':'MCB-C16A/1P','kind':'device','category':'MCB','bbox':[1,1,20,20]}])),json.dumps(ctx('overview')))
  p=json.loads(x['packet_json']);self.assertEqual(p['observations'],[]);self.assertEqual(len(p['quarantined_observations']),1)
 def test_unplaced_text_is_not_an_independent_item(self):
  p=json.loads(parse_view(json.dumps(raw([{'id':'X','raw':'MCB-C16A/1P','kind':'device','category':'MCB','bbox':None}])),json.dumps(ctx()))['packet_json'])
  self.assertFalse(p['observations']);self.assertEqual(p['quarantined_observations'][0]['raw'],'MCB-C16A/1P')
 def test_phase_prefix_merges_only_same_position_cross_views(self):
  rows=[{'raw':r,'bbox':b,'kind':'device','category':'MCB','page':1,'job_id':j,'local_id':str(i)} for i,(r,b,j) in enumerate([
   ('L1 MCB-C16A/1P',[100,100,200,120],'H2'),('MCB-C16A/1P',[101,101,201,121],'V2'),('MCB-C16A/1P',[100,150,200,170],'V2')])]
  groups,_=merge_positions(rows,'O');self.assertEqual(len(groups),2);self.assertEqual(len(groups[0]['observations']),2)
 def test_classification_disagreement_is_not_auto_resolved(self):
  a={'raw':'XYZ-100/4P','bbox':[10,10,80,25],'kind':'device','category':'SPD','page':1,'job_id':'H2','local_id':'1'};b=dict(a,category='controller',job_id='V2')
  groups,_=merge_positions([a,b],'O');self.assertEqual(groups[0]['category'],'unknown');self.assertEqual(len(groups[0]['classification_conflict']),2)
 def test_true_blank_is_recorded_without_model(self):
  b=base64.b64encode(png()).decode();c=crop_node(b,json.dumps(dict(ctx(),rect=[0,0,1000,400])))
  self.assertTrue(json.loads(c['context_json'])['crop_audit']['verified_blank'])
  k=json.loads(c['context_json']);self.assertEqual(check_view_files([{'filename':c['filename']}],json.dumps(k))['valid'],'BLANK')
  self.assertEqual(json.loads(blank_view(json.dumps(k))['packet_json'])['status'],'blank')
 def test_nonwhite_content_is_not_blank(self):
  b=base64.b64encode(png(100,100,(40,60,70,90))).decode();c=crop_node(b,json.dumps(dict(ctx(),rect=[0,0,1000,1000])))
  audit=json.loads(c['context_json'])['crop_audit'];self.assertFalse(audit['verified_blank']);self.assertGreater(audit['content_roi'][1],0)
 def test_local_prompt_has_no_native_text(self):
  from transport import j
  import zlib
  packet={'native_text':'EXTERNAL-MODEL-999','notes':'SOURCE-NOTE'}
  encoded=base64.b64encode(zlib.compress(j(packet).encode())).decode()
  context=job_context(json.dumps(ctx()),encoded)['context_json'];self.assertNotIn('EXTERNAL-MODEL',context);self.assertNotIn('SOURCE-NOTE',context)
if __name__=='__main__':unittest.main(verbosity=2)
