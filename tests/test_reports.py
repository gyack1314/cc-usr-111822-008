"""合作报告的不可覆盖、版本对比与更正来源说明。"""
import json
import unittest
from pathlib import Path

from battery_cooperation.evidence import connect, receive
from battery_cooperation.factors import from_json
from battery_cooperation.reports import history, issue, read

DATA = json.loads((Path(__file__).resolve().parents[1] / 'data' / 'example.json').read_text(encoding='utf-8'))

CERT_CORRECTION = {
    'kind': 'origin_certificate', 'id': 'C2', 'revision': 'r1',
    'received_at': '2026-10-09T09:00:00+08:00',
    'payload': {'lot': 'L2', 'origin_country': 'Vietnam', 'issued_on': '2026-10-09', 'evidence_ref': 'COO/1009'},
}
PLANT_CORRECTION = {
    'kind': 'plant', 'id': 'P2', 'revision': 'r2',
    'received_at': '2026-10-09T09:30:00+08:00',
    'payload': {'name': 'Batam 前驱体厂', 'country': 'Indonesia', 'commissioned_on': '2026-09-01',
                'capacity_tons_per_year': 80000},
}


class Reports(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        for item in DATA['documents']:
            receive(self.db, item)
        self.factors = from_json(DATA.get('factors'))
        self.first = issue(self.db, 'MY-Q3', DATA['known_at'], DATA['period'], self.factors)

    def tearDown(self):
        self.db.close()

    def correct(self):
        receive(self.db, CERT_CORRECTION)
        receive(self.db, PLANT_CORRECTION)
        return issue(self.db, 'MY-Q3', '2026-10-10T00:00:00+08:00', DATA['period'], self.factors)

    def test_first_revision_records_input_snapshot(self):
        self.assertEqual(self.first['revision'], 1)
        self.assertGreater(len(self.first['snapshot']), 0)
        self.assertEqual(self.first['changes']['from_revision'], None)

    def test_second_revision_does_not_overwrite_first(self):
        second = self.correct()
        self.assertEqual(second['revision'], 2)
        first_again = read(self.db, 'MY-Q3', 1)
        self.assertTrue(first_again['digest'])
        self.assertEqual(first_again['revision'], 1)
        self.assertEqual(first_again['period'], self.first['period'])
        self.assertEqual(
            [(row['lot'], row['origin_country']) for row in first_again['lots']],
            [(row['lot'], row['origin_country']) for row in self.first['lots']],
        )
        self.assertEqual(len(history(self.db, 'MY-Q3')), 2)

    def test_database_refuses_update_and_delete(self):
        with self.assertRaises(Exception):
            self.db.execute("UPDATE reports SET body='{}' WHERE name='MY-Q3' AND revision=1")
        with self.assertRaises(Exception):
            self.db.execute("DELETE FROM reports WHERE name='MY-Q3' AND revision=1")

    def test_new_revision_names_change_sources(self):
        second = self.correct()
        changes = second['changes']
        self.assertEqual(changes['from_revision'], 1)
        self.assertTrue(changes['region_changes'])
        origin_change = next(item for item in changes['region_changes'] if item['lot'] == 'L2')
        self.assertEqual(origin_change['to'], 'Vietnam')
        self.assertTrue(any(item['kind'] == 'origin_certificate' for item in changes['evidence_changes']))
        self.assertTrue(any(item['plant'] == 'P2' and item['change'] == '工厂状态或投用日期变化'
                            for item in changes['plant_changes']))
        self.assertTrue(changes['contribution_changes'])

    def test_region_attribution_follows_certificate(self):
        second = self.correct()
        lot = next(row for row in second['lots'] if row['lot'] == 'L2')
        self.assertEqual(lot['origin_country'], 'Vietnam')
        self.assertEqual(lot['origin_status'], '已核证')
        self.assertEqual(lot['origin_evidence_on'], '2026-10-09')
        first_lot = next(row for row in self.first['lots'] if row['lot'] == 'L2')
        self.assertEqual(first_lot['origin_country'], 'Indonesia')

    def test_known_at_only_sees_earlier_documents(self):
        receive(self.db, CERT_CORRECTION)
        replay = issue(self.db, 'MY-Q3', DATA['known_at'], DATA['period'], self.factors)
        lot = next(row for row in replay['lots'] if row['lot'] == 'L2')
        self.assertEqual(lot['origin_country'], 'Indonesia')
        self.assertEqual(replay['revision'], 2)

    def test_backdated_revision_rejected(self):
        with self.assertRaises(ValueError):
            issue(self.db, 'MY-Q3', '2026-10-01T00:00:00+08:00', DATA['period'], self.factors)

    def test_changing_period_under_same_name_rejected(self):
        with self.assertRaises(ValueError):
            issue(self.db, 'MY-Q3', '2026-10-10T00:00:00+08:00', {'start': '2026-01-01', 'end': '2026-06-30'}, self.factors)

    def test_commitments_listed_separately_from_realized(self):
        self.assertIn('commitments', self.first)
        statuses = {row['commitment']: row['status'] for row in self.first['commitments']}
        self.assertEqual(statuses['CM-JOBS'], '未到期')
        self.assertEqual(statuses['CM-POWER'], '未兑现')
        self.assertNotIn('commitments', self.first['countries'][0])

    def test_unverified_and_pending_lists_present(self):
        self.assertIn('pending_documents', self.first)
        self.assertIn('unverified_documents', self.first)
        self.assertIn('origin_issues', self.first)


if __name__ == '__main__':
    unittest.main()
