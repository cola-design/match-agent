import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from product_sync.sync import accessory_comparison_key,clean_product,decide,signature


def raw(specs):
    return {'ITEM_NUM':'00001234','ITEM_ID':'123','ITEM_DESC':'fixture',
            'CATEGORY_NAME':'配电断路器类.塑壳断路器.TEST','CATEGORY_CODE':'330.1.2',
            'SPEC_STRING':specs,'STATUS':'有效','LAST_UPDATE_DATE':'2026-10-20 00:00:00.0'}


def attr(name,value):
    return {'ATTR_NAME':name,'ATTR_VALUE':value,'STATUS':'有效','ATTR_ID':'fixture','SA_ID':'fixture',
            'LAST_UPDATE_DATE':'2026-10-20 00:00:00.0'}


PAIRS=(
    ('00 无附件','无'),('08 报警触头','报警触头(08)'),('10 分励线圈','分励线圈(10)'),
    ('18 分励脱扣器+报警触头','分励+报警(18)'),('20 辅助触头','辅助触头(20)'),
    ('28 辅助报警触头','辅助+报警(28)'),('30 欠压线圈','欠压线圈(30)'),
    ('38 欠压脱扣器+报警触头','欠压+报警(38)'),('40 分励脱扣器+辅助触头','分励+辅助(40)'),
    ('48 分励脱扣器+辅助报警触头','分励+辅报(48)'),('50 分励脱扣器+欠压脱扣器','分励+欠压(50)'),
    ('60 双组辅助触头','双组辅助(60)'),('68 辅助触头+辅助报警触头','辅助+辅报(68)'),
    ('70 欠压脱扣器+辅助触头','欠压+辅助(70)'),('78 欠压脱扣器+辅助报警触头','欠压+辅报(78)'),
)


class AccessoryComparisonTest(unittest.TestCase):
    def test_known_pairs_preserve_both_source_texts_without_conflict(self):
        for first,second in PAIRS:
            with self.subTest(first=first,second=second):
                product,_=clean_product(raw('附件代号&'+first),[attr('附件代码',second)])
                self.assertNotIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])
                self.assertEqual([a['value'] for a in product['source_attributes']],[first,second])
                self.assertEqual([v['value_text'] for v in product['standard_specs']['accessory_code']],
                                 [first,second])

    def test_same_rules_compare_old_and_new_display_and_keep_approval(self):
        for first,second in PAIRS:
            with self.subTest(first=first):
                old,_=clean_product(raw('附件代号&'+first),[attr('附件代码',second)])
                old.update(data_status='ACTIVE',publication_basis='REVIEWED_FIXTURE')
                incoming=raw('附件代码&'+second);incoming['LAST_UPDATE_DATE']='2026-10-21 00:00:00.0'
                candidate,version=clean_product(incoming,[attr('附件代号',first)])
                self.assertEqual(signature(old),signature(candidate))
                action,updated=decide(old,candidate,version,'r1')
                self.assertEqual(action,'metadata_only');self.assertEqual(updated['data_status'],'ACTIVE')
                self.assertEqual(decide(updated,candidate,version,'r2'),('unchanged',None))

    def test_code_without_known_matching_label_is_not_sufficient(self):
        cases=[('20 辅助触头','报警触头(20)'),('20 辅助触头','辅助触头(08)'),
               ('20 辅助触头','20'),('99 未知附件','未知附件(99)'),
               ('20 辅助触头','辅助触头(20) AC220V'),('00 无附件','辅助触头(00)'),
               ('20 辅助触头','辅助触头(020)')]
        for first,second in cases:
            with self.subTest(first=first,second=second):
                product,_=clean_product(raw('附件代号&'+first),[attr('附件代码',second)])
                self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])

    def test_whitespace_and_fullwidth_punctuation_of_known_label(self):
        self.assertEqual(accessory_comparison_key('２０ 辅助触头'),accessory_comparison_key('辅助触头（２０）'))
        self.assertNotEqual(accessory_comparison_key('未知（２０）'),accessory_comparison_key('20 未知'))

    def test_real_electrical_differences_remain_blocked_with_equivalent_accessories(self):
        for field,first,second in [('分断能力','L 25kA','L 35kA'),('壳架','160','250'),
                                   ('极数','3P','4P A型'),('极数','2P','2P C型'),
                                   ('额定电流','100A','125A'),('额定电压','AC220V','AC380V')]:
            with self.subTest(field=field):
                product,_=clean_product(raw('附件代号&20 辅助触头@'+field+'&'+first),
                                        [attr('附件代码','辅助触头(20)'),attr(field,second)])
                self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])

    def test_unconfirmed_breaking_capacity_labels_remain_strict(self):
        product,_=clean_product(raw('分断能力&L 80kA'),[attr('分断能力','常规分断 80kA')])
        self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])

    def test_changed_accessory_and_unknown_changes_still_require_review(self):
        old,_=clean_product(raw('附件代号&20 辅助触头'),[]);old['data_status']='ACTIVE'
        for replacement in ('08 报警触头','99 未知附件','辅助触头(20) AC220V'):
            with self.subTest(replacement=replacement):
                candidate,version=clean_product(raw('附件代号&'+replacement),[])
                action,updated=decide(old,candidate,version,'r1')
                self.assertEqual(action,'changed');self.assertEqual(updated['data_status'],'PENDING_REVIEW')

    def test_no_accessory_number_rule_is_applied_to_other_attributes(self):
        product,_=clean_product(raw('特殊说明&20 辅助触头'),[attr('特殊说明','辅助触头(20)')])
        self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])

    def test_signature_keeps_source_coverage_and_other_attributes(self):
        original,_=clean_product(raw('附件代号&20 辅助触头'),[])
        with_confirmation,_=clean_product(raw('附件代号&20 辅助触头'),[attr('附件代码','辅助触头(20)')])
        self.assertNotEqual(signature(original),signature(with_confirmation))
        changed=copy.deepcopy(original);changed['description']='other model'
        self.assertNotEqual(signature(original),signature(changed))

    def test_comparison_does_not_mutate_input(self):
        product,_=clean_product(raw('附件代号&20 辅助触头'),[attr('附件代码','辅助触头(20)')])
        before=copy.deepcopy(product);signature(product)
        self.assertEqual(product,before)

    def test_multiple_different_type2_values_remain_duplicate_and_conflict(self):
        product,_=clean_product(raw('附件代号&20 辅助触头'),
                                [attr('附件代码','辅助触头(20)'),attr('附件代号','报警触头(08)')])
        self.assertIn('DUPLICATE_TYPE2_ATTRIBUTE',product['quality_flags'])
        self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])


if __name__=='__main__':unittest.main()
