"""地区贡献、期间归属、量纲分离与承诺单列的规则核对。"""
import copy
import json
import unittest
from pathlib import Path

from battery_cooperation.commitments import outstanding
from battery_cooperation.contribution import contribution
from battery_cooperation.evidence import connect, receive
from battery_cooperation.factors import from_json
from battery_cooperation.models import load, plant_state

DATA = json.loads((Path(__file__).resolve().parents[1] / 'data' / 'example.json').read_text(encoding='utf-8'))


class ChainBase(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        self.load(DATA)
        self.factors = from_json(DATA.get('factors'))

    def tearDown(self):
        self.db.close()

    def load(self, data):
        for item in data['documents']:
            receive(self.db, item)

    def model(self, known_at=None):
        return load(self.db, known_at or DATA['known_at'])

    def run_period(self, period=None, known_at=None):
        period = period or DATA['period']
        model = self.model(known_at)
        result = contribution(model, period, self.factors)
        return model, result, outstanding(model, period, self.factors, result)

    def country(self, result, name):
        return next(row for row in result['countries'] if row['country'] == name)


class Chain(ChainBase):
    def test_contribution_by_country(self):
        _, result, _ = self.run_period()
        malaysia = self.country(result, 'Malaysia')
        self.assertEqual(malaysia['lots'], 2)
        self.assertEqual(malaysia['processed_tons'], 100)
        self.assertEqual(malaysia['power_coverage'], 0.85)
        self.assertEqual(malaysia['service_regions'], ['Kedah', 'Penang', 'Perak'])
        self.assertNotIn('Indonesia', [row['country'] for row in result['countries']])

    def test_low_base_ranges_are_ordered(self):
        _, result, _ = self.run_period()
        malaysia = self.country(result, 'Malaysia')
        for key in ('jobs', 'power_mwh', 'tax', 'revenue', 'investment'):
            self.assertLessEqual(malaysia[key]['low'], malaysia[key]['base'], key)

    def test_scopes_are_not_added_together(self):
        _, result, _ = self.run_period()
        malaysia = self.country(result, 'Malaysia')
        self.assertNotEqual(malaysia['tax']['base'], malaysia['revenue']['base'])
        self.assertIn('original_currency', malaysia['tax'])
        self.assertEqual(malaysia['tax']['original_currency']['MYR'], 350000)
        self.assertEqual(malaysia['investment']['base'], 0)

    def test_uncommissioned_plant_excluded_but_listed(self):
        _, result, _ = self.run_period()
        indonesia_lots = [row for row in result['lots'] if row['country'] == 'Indonesia']
        self.assertTrue(indonesia_lots)
        self.assertTrue(all(row['plant_status'] == '待投用' for row in indonesia_lots))
        self.assertEqual(self.country(result, 'Malaysia')['processed_tons'], 100)
        self.assertFalse(any(row['processed_in'] == 'Indonesia' for row in result['flows']))

    def test_period_before_commissioning_is_not_counted(self):
        _, result, _ = self.run_period({'start': '2026-01-01', 'end': '2026-06-30'})
        self.assertEqual(result['countries'], [])

    def test_unverified_record_is_not_counted(self):
        _, result, _ = self.run_period()
        malaysia = self.country(result, 'Malaysia')
        # E3 落在 10 月，不在报告期，不出现；跨期的 E2 与 E1 分属不同期间，均计入。
        self.assertAlmostEqual(malaysia['jobs']['base'], 93.0, places=1)


class ValuationAndCommitments(ChainBase):
    def test_missing_factor_keeps_original_currency_only(self):
        data = copy.deepcopy(DATA)
        data['factors']['rates'].pop('tax:MYR:tax')
        db = connect()
        for item in data['documents']:
            receive(db, item)
        model = load(db, data['known_at'])
        factors = from_json(data['factors'])
        result = contribution(model, data['period'], factors)
        malaysia = next(row for row in result['countries'] if row['country'] == 'Malaysia')
        self.assertEqual(malaysia['tax']['base'], 0)
        self.assertEqual(malaysia['tax']['original_currency']['MYR'], 350000)
        self.assertTrue(any(item['kind'] == 'tax' for item in result['unverified_documents']))
        db.close()

    def test_fiscal_year_period_resolves(self):
        data = copy.deepcopy(DATA)
        for item in data['documents']:
            if item['kind'] == 'tax':
                item['payload'].pop('period_start')
                item['payload'].pop('period_end')
                item['payload'].update({'fiscal_year': '2026', 'jurisdiction': 'Malaysia'})
        db = connect()
        for item in data['documents']:
            receive(db, item)
        model = load(db, data['known_at'])
        result = contribution(model, data['period'], from_json(data['factors']))
        malaysia = next(row for row in result['countries'] if row['country'] == 'Malaysia')
        self.assertEqual(malaysia['tax']['base'], 77000)
        db.close()

    def test_unknown_fiscal_period_is_pending(self):
        data = copy.deepcopy(DATA)
        for item in data['documents']:
            if item['kind'] == 'tax':
                item['payload'].pop('period_start')
                item['payload'].pop('period_end')
                item['payload'].update({'fiscal_year': '2027', 'jurisdiction': 'Malaysia'})
        db = connect()
        for item in data['documents']:
            receive(db, item)
        model = load(db, data['known_at'])
        result = contribution(model, data['period'], from_json(data['factors']))
        malaysia = next(row for row in result['countries'] if row['country'] == 'Malaysia')
        self.assertEqual(malaysia['tax']['base'], 0)
        self.assertTrue(any(item['kind'] == 'tax' for item in result['pending_documents']))
        db.close()

    def test_future_commitment_is_not_deducted(self):
        _, _, rows = self.run_period()
        jobs = next(row for row in rows if row['commitment'] == 'CM-JOBS')
        self.assertEqual(jobs['status'], '未到期')
        self.assertIsNone(jobs['outstanding'])

    def test_due_commitment_reports_shortfall_only(self):
        _, _, rows = self.run_period()
        power = next(row for row in rows if row['commitment'] == 'CM-POWER')
        self.assertEqual(power['status'], '未兑现')
        self.assertAlmostEqual(power['outstanding'], 5000 - 1695.7, places=1)

    def test_commitment_uses_its_own_plant_not_country_total(self):
        _, _, rows = self.run_period()
        cap = next(row for row in rows if row['commitment'] == 'CM-CAP')
        self.assertEqual(cap['plant'], 'P2')
        self.assertEqual(cap['realized'], 0)
        self.assertEqual(cap['status'], '未到期')

    def test_commitment_original_currency_is_kept(self):
        _, _, rows = self.run_period()
        tax = next(row for row in rows if row['commitment'] == 'CM-TAX')
        self.assertEqual(tax['target_currency'], 'MYR')
        self.assertGreater(tax['target_compared'], 0)


class Corrections(ChainBase):
    def test_certificate_correction_changes_origin(self):
        _, before, _ = self.run_period()
        lot = next(row for row in before['lots'] if row['lot'] == 'L2')
        self.assertEqual(lot['origin_country'], 'Indonesia')
        self.assertEqual(lot['origin_status'], '仅申报')
        receive(self.db, {
            'kind': 'origin_certificate', 'id': 'C2', 'revision': 'r1',
            'received_at': '2026-10-09T09:00:00+08:00',
            'payload': {'lot': 'L2', 'origin_country': 'Vietnam', 'issued_on': '2026-10-09', 'evidence_ref': 'COO/1009'},
        })
        _, after, _ = self.run_period(known_at='2026-10-10T00:00:00+08:00')
        lot = next(row for row in after['lots'] if row['lot'] == 'L2')
        self.assertEqual(lot['origin_country'], 'Vietnam')
        self.assertEqual(lot['origin_status'], '已核证')
        self.assertEqual(self.country(after, 'Malaysia')['processed_tons'], 100)

    def test_commissioning_correction_moves_capacity(self):
        _, before, _ = self.run_period()
        self.assertNotIn('Indonesia', [row['country'] for row in before['countries']])
        receive(self.db, {
            'kind': 'plant', 'id': 'P2', 'revision': 'r2',
            'received_at': '2026-10-09T09:30:00+08:00',
            'payload': {'name': 'Batam', 'country': 'Indonesia', 'commissioned_on': '2026-09-01',
                        'capacity_tons_per_year': 80000},
        })
        model, after, _ = self.run_period(known_at='2026-10-10T00:00:00+08:00')
        indonesia = self.country(after, 'Indonesia')
        self.assertGreater(indonesia['capacity_tons_per_year'], 0)
        self.assertEqual(indonesia['processed_tons'], 25)
        self.assertEqual(plant_state(model.plants['P2'], DATA['period']), '已投用')

    def test_old_result_is_stable_after_correction(self):
        _, before, _ = self.run_period()
        receive(self.db, {
            'kind': 'plant', 'id': 'P2', 'revision': 'r2',
            'received_at': '2026-10-09T09:30:00+08:00',
            'payload': {'name': 'Batam', 'country': 'Indonesia', 'commissioned_on': '2026-09-01'},
        })
        _, replay, _ = self.run_period(known_at=DATA['known_at'])
        self.assertEqual(before['countries'], replay['countries'])

    def test_duplicate_employment_period_not_double_counted(self):
        receive(self.db, {
            'kind': 'employment', 'id': 'E1b', 'revision': 'r1',
            'received_at': '2026-10-04T12:00:00+08:00',
            'payload': {'plant': 'P1', 'period_start': '2026-09-01', 'period_end': '2026-09-30',
                        'local_employees': 120, 'evidence_ref': 'HR/2026/09-v2'},
        })
        _, result, _ = self.run_period()
        malaysia = self.country(result, 'Malaysia')
        self.assertEqual(malaysia['jobs']['base'], 93.0)
        self.assertTrue(any(item['document'] == 'E1' and '重叠' in item['reason']
                            for item in result['pending_documents']))


if __name__ == '__main__':
    unittest.main()


class InvalidCorrections(ChainBase):
    def test_invalid_new_revision_falls_back_to_last_valid(self):
        receive(self.db, {
            'kind': 'plant', 'id': 'P1', 'revision': 'r2',
            'received_at': '2026-10-09T00:00:00+08:00',
            'payload': {'commissioned_on': '2026-09-01'},
        })
        _, result, _ = self.run_period(known_at='2026-10-10T00:00:00+08:00')
        self.assertEqual(self.country(result, 'Malaysia')['processed_tons'], 100)
        self.assertTrue(any(item['document'] == 'plant:P1' and item['used_revision'] == 'r1'
                            for item in result['fallback_documents']))

    def test_unknown_kind_reported_not_silently_dropped(self):
        receive(self.db, {
            'kind': 'mystery', 'id': 'M1', 'revision': 'r1',
            'received_at': '2026-10-02T00:00:00+08:00', 'payload': {},
        })
        _, result, _ = self.run_period()
        self.assertTrue(any(item['document'] == 'mystery:M1' for item in result['unsupported_documents']))

    def test_shipment_without_movement_date_is_not_counted(self):
        receive(self.db, {
            'kind': 'shipment', 'id': 'L9', 'revision': 'r1',
            'received_at': '2026-10-02T00:00:00+08:00',
            'payload': {'lot': 'L9', 'origin': 'Indonesia', 'plant': 'P1', 'tons': 999},
        })
        _, result, _ = self.run_period()
        self.assertEqual(self.country(result, 'Malaysia')['processed_tons'], 100)
        row = next(item for item in result['lots'] if item['lot'] == 'L9')
        self.assertEqual(row['period_note'], '缺少加工或发运日期，无法归属期间')
