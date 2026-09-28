"""Tests des formules.

Les séries utilisées ici sont construites à la main ou tirées d'un générateur
aléatoire À SEULE FIN DE TEST. Elles ne quittent jamais ce fichier et ne sont
jamais écrites dans data/.

  python -m unittest discover -s scripts/tests -v
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import indicators as ind  # noqa: E402
import scoring as sc  # noqa: E402


def _test_frame(n=600, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.01, n)))
    lo = c * (1 - np.abs(rng.normal(0, 0.01, n)))
    o = c * (1 + rng.normal(0, 0.004, n))
    v = rng.lognormal(15, 0.3, n)
    df = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=idx)
    b = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n))), index=idx)
    return df, b


class VolumeAcceleration(unittest.TestCase):
    def _score(self, tail):
        base = [5e6] * 25
        v = pd.Series(base + tail, dtype=float, index=pd.bdate_range("2024-01-01", periods=25 + len(tail)))
        return ind.volume_acceleration(v)["vacc_score"].iloc[-1]

    def test_ramp_beats_single_spike(self):
        ramp = self._score([5e6, 6e6, 7e6, 9e6, 13e6])
        spike = self._score([5e6, 5e6, 5e6, 5e6, 13e6])
        self.assertGreater(ramp, 90)
        self.assertLess(spike, 50)
        self.assertGreater(ramp, 2 * spike)

    def test_earnings_spike_is_not_a_ramp(self):
        # un seul jour à 8,8× la moyenne (publication de résultats) après des séances normales
        spike = self._score([5e6, 5.5e6, 5e6, 5.2e6, 44e6])
        self.assertLess(spike, 40)

    def test_flat_volume_scores_zero(self):
        self.assertEqual(self._score([5e6] * 5), 0)

    def test_falling_volume_scores_zero(self):
        self.assertLess(self._score([9e6, 8e6, 7e6, 6e6, 5e6]), 5)


class Basics(unittest.TestCase):
    def test_rsi_bounds_and_uptrend(self):
        up = pd.Series(np.arange(1, 60, dtype=float))
        self.assertAlmostEqual(ind.rsi(up, 14).iloc[-1], 100.0)
        df, _ = _test_frame()
        r = ind.rsi(df["close"], 14).dropna()
        self.assertTrue(((r >= 0) & (r <= 100)).all())

    def test_linreg_exact_line(self):
        y = pd.Series([1.0, 3.0, 5.0, 7.0, 9.0])
        slope, r2 = ind.rolling_linreg(y, 5)
        self.assertAlmostEqual(slope.iloc[-1], 2.0)
        self.assertAlmostEqual(r2.iloc[-1], 1.0)

    def test_wavg_ignores_nan(self):
        a = pd.Series([100.0, np.nan])
        b = pd.Series([0.0, 50.0])
        out = ind.wavg([(1, a), (1, b)])
        self.assertAlmostEqual(out.iloc[0], 50.0)
        self.assertAlmostEqual(out.iloc[1], 50.0)

    def test_days_since(self):
        e = pd.Series([False, True, False, False, True, False])
        self.assertEqual(list(ind.days_since(e).fillna(-1)), [-1, 0, 1, 2, 0, 1])


class NoLookAhead(unittest.TestCase):
    """Le score à la date T doit être identique qu'on connaisse ou non la suite."""

    def test_truncated_equals_full(self):
        df, b = _test_frame(700, seed=3)
        cfg = sc.load_config()
        full_x = ind.compute(df, b)
        full = sc.score_frame(full_x, cfg)
        for k in (320, 450, 610):
            part_x = ind.compute(df.iloc[:k], b.iloc[:k])
            part = sc.score_frame(part_x, cfg)
            for col in ("score_standard", "score_swing", "score_long_term", "c_volume_momentum",
                        "c_breakout_potential", "c_relative_strength"):
                a, f = part[col].iloc[-1], full[col].iloc[k - 1]
                if np.isnan(a):
                    self.assertTrue(np.isnan(f), col)
                else:
                    self.assertAlmostEqual(a, f, places=6, msg=f"{col} à l'index {k}")


class Overextension(unittest.TestCase):
    def test_penalty(self):
        cfg = sc.load_config()
        x = pd.DataFrame({"rsi14": [60.0, 85.0], "ext_atr": [1.0, 6.0], "ret_1y": [0.3, 1.5]})
        pen = sc.overextension(x, cfg)
        self.assertEqual(pen.iloc[0], 1.0)
        self.assertLess(pen.iloc[1], 0.65)
        self.assertGreaterEqual(pen.iloc[1], cfg["overextension"]["floor"])


class Config(unittest.TestCase):
    def test_weights_sum_to_100(self):
        cfg = sc.load_config()
        for p in cfg["profiles"].values():
            self.assertAlmostEqual(sum(p["weights"].values()), 100)


if __name__ == "__main__":
    unittest.main()
