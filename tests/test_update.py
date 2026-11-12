import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from update import ingest_monthly,level_curve,level_credit,level_fed

class TestSafety(unittest.TestCase):
    def test_monthly_same_period_not_overwritten(self):
        old=[{'period':'2026-09','released_at':'2026-10-22','value':99.6,'source':'official'}]
        self.assertFalse(ingest_monthly(old, {'period':'2026-09','released_at':'2026-10-22','value':88.0,'source':'official'}))
        self.assertEqual(old[0]['value'],99.6)
    def test_new_month_only_on_official_release(self):
        old=[{'period':'2026-09','released_at':'2026-10-22','value':99.6,'source':'official'}]
        self.assertTrue(ingest_monthly(old, {'period':'2026-10','released_at':'2026-11-19','value':98.5,'source':'official'}))
        self.assertEqual(len(old),2)
    def test_official_revision_only_new_release(self):
        old=[{'period':'2026-09','released_at':'2026-10-22','value':99.6,'source':'official'}]
        self.assertTrue(ingest_monthly(old, {'period':'2026-09','released_at':'2026-11-19','value':99.7,'source':'official', 'revision':True}))
        self.assertEqual(old[0]['value'],99.7)
    def test_curve_needs_history(self):
        self.assertEqual(level_curve([{'date':'2026-11-01','value':0.3}])[0],'unknown')
    def test_credit_limits(self):
        a=[{'date':'2026-01-01','value':x} for x in ([3.0]*22 + [7.4])]
        self.assertEqual(level_credit(a)[0],'danger')
    def test_fed_needs_history(self):
        self.assertEqual(level_fed([])[0],'unknown')

if __name__=='__main__': unittest.main()
