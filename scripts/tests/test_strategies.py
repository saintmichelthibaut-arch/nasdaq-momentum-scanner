"""Tests des stratégies A et B sur des séries de prix construites à la main.

Les séries ci-dessous sont des cas d'école (pas des données de marché) : elles
servent uniquement à vérifier que les formules réagissent comme prévu.
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import indicators as ind  # noqa: E402
import strategies as stg  # noqa: E402


def frame(closes, vols):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    o = c.shift(1).fillna(c.iloc[0])
    h = np.maximum(o, c) * 1.005
    lo = np.minimum(o, c) * 0.995
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": pd.Series(vols, index=idx, dtype=float)})


def crdo_like():
    """Montée régulière → plus haut → -35 % en 5 semaines → base → 4 hausses sur volume."""
    up = list(np.linspace(50, 100, 220))
    drop = list(np.linspace(100, 65, 25))
    base = [66, 64.5, 65.5, 64, 66, 65, 67, 66, 68, 67, 68.5, 67.5]
    rally = [70, 72.5, 75, 77.5]
    closes = up + drop + base + rally
    vols = [1e6] * (len(up) + len(drop)) + [0.7e6] * len(base) + [1.6e6, 1.8e6, 2.0e6, 2.2e6]
    return frame(closes, vols)


def falling_knife():
    up = list(np.linspace(50, 100, 220))
    drop = list(np.linspace(100, 55, 40))
    return frame(up + drop, [1e6] * len(up) + [1.5e6] * len(drop))


def compute(df):
    bench = pd.Series(np.linspace(100, 110, len(df)), index=df.index)
    return ind.compute(df, bench)


class StrategyB(unittest.TestCase):
    def test_reversal_detected(self):
        x = compute(crdo_like())
        b = stg.strategy_b(x, None, True)
        self.assertLess(b["max_drawdown"], -0.30)
        self.assertEqual(b["phase"], "reversal")
        self.assertGreaterEqual(b["up_streak"], 4)
        self.assertTrue(b["signals"]["confirmation"]["break_10d_high"])
        self.assertGreaterEqual(b["score"], 60)
        self.assertGreaterEqual(b["crdo"]["count"], 9)
        self.assertTrue(b["crdo"]["flag"])
        # sans fondamentaux : critère N/A, pas de point inventé
        self.assertEqual(b["points"]["fundamentals"], 0)
        self.assertIsNone(b["quality_dip"]["flag"])

    def test_falling_knife_gets_no_signal(self):
        x = compute(falling_knife())
        b = stg.strategy_b(x, None, True)
        self.assertEqual(b["phase"], "knife")
        self.assertLessEqual(b["score"], 25)
        self.assertFalse(b["crdo"]["flag"])

    def test_point_in_time(self):
        x = compute(crdo_like())
        i = len(x) - 6
        full = stg.rebound_at(x, i)
        cut = stg.rebound_at(compute(crdo_like().iloc[: i + 1]), i)
        for k in ("peak", "trough", "max_dd", "streak", "ups5", "higher_low", "brk10", "above20"):
            self.assertEqual(full[k], cut[k], k)

    def test_quality_dip_needs_real_fundamentals(self):
        x = compute(crdo_like())
        f = {"market_cap": 5e10, "revenue_growth": 0.35, "earnings_growth": 0.4, "total_cash": 2e9,
             "total_debt": 1e9, "recommendation_mean": 1.8, "target_mean": 95.0, "free_cashflow": 1e8,
             "operating_margin": 0.2}
        b = stg.strategy_b(x, f, True)
        self.assertTrue(b["quality_dip"]["flag"])
        self.assertGreater(b["points"]["fundamentals"], 0)
        f_bad = dict(f, revenue_growth=-0.2)
        self.assertFalse(stg.strategy_b(x, f_bad, True)["quality_dip"]["flag"])


class StrategyA(unittest.TestCase):
    def test_scores_bounded_and_na_without_data(self):
        x = compute(crdo_like())
        srow = pd.Series({"penalty": 1.0})
        a = stg.strategy_a(x, srow, None, {"spy_ret20": None, "etf_ret20": None, "peer_pct": None, "sector_breadth": None})
        for k, mx in stg.A_MAX.items():
            self.assertLessEqual(a["points"][k], mx)
        self.assertEqual(a["points"]["positioning"], 0)
        self.assertIn("N/A", a["notes"]["positioning"])
        self.assertLessEqual(a["score"], 100)

    def test_analyst_catalyst(self):
        x = compute(crdo_like())
        d = x.index[-3].strftime("%Y-%m-%d")
        f = {"market_cap": 5e10, "revenue_growth": 0.3, "analyst_actions": [
            {"date": d, "firm": "Firm A", "action": "up", "to": "Buy", "from": "Hold", "pt_action": "Raises", "pt": 90, "pt_prior": 80}]}
        a = stg.strategy_a(x, pd.Series({"penalty": 1.0}), f, {"spy_ret20": 0.0, "etf_ret20": 0.0, "peer_pct": 0.8, "sector_breadth": 0.5})
        self.assertGreaterEqual(a["points"]["catalyst"], 8)
        self.assertTrue(any("Firm A" in c for c in a["catalysts"]))


if __name__ == "__main__":
    unittest.main()
