"""Indicateurs techniques, en séries temporelles vectorisées.

Chaque fonction reçoit l'historique d'UN titre et renvoie une valeur par séance.
La valeur à la date T n'utilise que des données <= T (pas de look-ahead) :
c'est ce qui permet au backtest de rejouer exactement le même calcul.

Conventions :
  - les rendements et distances sont des fractions (0.05 = +5 %)
  - les pentes de moyennes mobiles sont en % par séance
  - NaN = pas assez d'historique pour calculer
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

RET_WINDOWS = {"1d": 1, "5d": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}
RS_WINDOWS = {"5d": 5, "20d": 20, "60d": 60, "6m": 126, "1y": 252}
BREAKOUT_WINDOWS = {"20d": 20, "50d": 50, "100d": 100, "52w": 252}


# ── briques de base ─────────────────────────────────────────────────────────

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder(-d.clip(upper=0), n)
    out = 100 - 100 / (1 + up / dn)
    out = out.where(dn != 0, 100.0)
    return out.where(up.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.where(prev.notna(), high - low)


def lin(x, lo: float, hi: float):
    """Projection linéaire de x sur [0, 1] : lo -> 0, hi -> 1 (hi peut être < lo)."""
    return ((x - lo) / (hi - lo)).clip(0, 1)


def streak(cond: pd.Series) -> pd.Series:
    """Nombre de séances consécutives (jusqu'à T inclus) où cond est vraie."""
    c = cond.fillna(False).astype(bool)
    return c.groupby((~c).cumsum()).cumsum().astype(float)


def days_since(event: pd.Series) -> pd.Series:
    """Séances écoulées depuis le dernier événement vrai (0 = aujourd'hui, NaN = jamais)."""
    e = event.fillna(False).astype(bool).to_numpy()
    idx = np.arange(len(e))
    last = np.where(e, idx, -1)
    last = np.maximum.accumulate(last)
    out = np.where(last >= 0, idx - last, np.nan).astype(float)
    return pd.Series(out, index=event.index)


def rolling_linreg(y: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """Régression linéaire glissante de y sur 0..n-1. Renvoie (pente, R²)."""
    arr = y.to_numpy(dtype=float)
    slope = np.full(len(arr), np.nan)
    r2 = np.full(len(arr), np.nan)
    if len(arr) >= n:
        w = sliding_window_view(arr, n)
        xc = np.arange(n) - (n - 1) / 2.0
        sxx = float((xc ** 2).sum())
        ym = w.mean(axis=1)
        sxy = (w * xc).sum(axis=1)
        b = sxy / sxx
        sst = ((w - ym[:, None]) ** 2).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            rr = np.where(sst > 0, b * b * sxx / sst, 0.0)
        slope[n - 1:] = b
        r2[n - 1:] = rr
    return pd.Series(slope, index=y.index), pd.Series(r2, index=y.index)


def wavg(parts: list[tuple[float, pd.Series]], min_share: float = 0.0) -> pd.Series:
    """Moyenne pondérée qui ignore les termes NaN (poids renormalisés).
    NaN si la part de poids disponible est < min_share."""
    num = None
    den = None
    total = sum(w for w, _ in parts)
    for w, s in parts:
        s = s.astype(float)
        avail = s.notna().astype(float) * w
        val = s.fillna(0.0) * w
        num = val if num is None else num + val
        den = avail if den is None else den + avail
    out = num / den.replace(0, np.nan)
    return out.where(den >= min_share * total)


# ── volume acceleration ─────────────────────────────────────────────────────

def volume_acceleration(volume: pd.Series) -> pd.DataFrame:
    """Est-ce que le volume lui-même accélère ?

    Régressions linéaires sur 5 et 10 séances, pente normalisée par la moyenne
    20 séances précédentes (en % de cette moyenne par séance), pondérée par R².
    Le R² (au carré) récompense une rampe régulière : 5M→6M→7M→9M→13M score
    très haut, une séance isolée à 13M score bas car la droite l'explique mal.
    """
    v = volume.astype(float)
    avg20_prev = sma(v, 20).shift(1)
    # Un pic isolé (résultats, news) ne doit pas passer pour une rampe :
    # volumes plafonnés à 3× la moyenne, et la hausse doit toucher plusieurs séances.
    vc = v.clip(upper=3 * avg20_prev)
    s5, r5 = rolling_linreg(vc, 5)
    s10, r10 = rolling_linreg(vc, 10)
    s5n = s5 / avg20_prev * 100
    s10n = s10 / avg20_prev * 100
    breadth = (v > 1.15 * avg20_prev).astype(float).rolling(5, min_periods=5).sum()
    raw = (0.6 * s5n.clip(lower=0) * r5 ** 2 + 0.4 * s10n.clip(lower=0) * r10 ** 2) * lin(breadth, 0, 3)
    score = lin(raw, 0, 15) * 100
    return pd.DataFrame({
        "vacc_slope5": s5n,
        "vacc_r2_5": r5,
        "vacc_slope10": s10n,
        "vacc_r2_10": r10,
        "vacc_raw": raw,
        "vacc_score": score.where(avg20_prev.notna() & breadth.notna()),
        "vacc_breadth5": breadth,
        "vol_up_streak": streak(v > v.shift(1)),
    })


# ── calcul complet pour un titre ────────────────────────────────────────────

def compute(df: pd.DataFrame, bench: pd.Series, local_bench: pd.Series | None = None) -> pd.DataFrame:
    """df : colonnes open, high, low, close, volume (index = dates de séance).
    bench : clôtures du benchmark (QQQ). local_bench : indice local (ex. CAC 40).
    Renvoie un DataFrame d'indicateurs aligné sur df.index."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    v = df["volume"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["open"], out["high"], out["low"], out["close"], out["volume"] = o, h, l, c, v

    # PRIX
    for k, n in RET_WINDOWS.items():
        out[f"ret_{k}"] = c / c.shift(n) - 1
    out["high_52w"] = h.rolling(252, min_periods=252).max()
    out["low_52w"] = l.rolling(252, min_periods=252).min()
    out["dist_high_52w"] = c / out["high_52w"] - 1

    # VOLUME
    out["vol_avg20"] = sma(v, 20)
    out["vol_avg50"] = sma(v, 50)
    out["rvol"] = v / sma(v, 20).shift(1)
    out["rvol5_50"] = sma(v, 5) / sma(v, 50).shift(5)
    out["vratio5"] = sma(v, 5) / sma(v, 20).shift(5)
    out["vol10_trend"] = sma(v, 10) / sma(v, 10).shift(10)
    up_day = c > c.shift(1)
    dn_day = c < c.shift(1)
    upv = v.where(up_day, 0).rolling(20, min_periods=20).sum()
    dnv = v.where(dn_day, 0).rolling(20, min_periods=20).sum()
    out["updown_vol20"] = upv / dnv.replace(0, np.nan)
    out = out.join(volume_acceleration(v))

    # MOMENTUM
    out["rsi14"] = rsi(c, 14)
    out["rsi7"] = rsi(c, 7)
    macd = ema(c, 12) - ema(c, 26)
    sig = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd"], out["macd_signal"], out["macd_hist"] = macd, sig, macd - sig
    out["roc20"] = c / c.shift(20) - 1
    out["roc60"] = c / c.shift(60) - 1

    # MOYENNES
    for n in (20, 50, 100, 200):
        out[f"sma{n}"] = sma(c, n)
    out["ema20"], out["ema50"] = ema(c, 20), ema(c, 50)
    out["slope_sma20"] = (out["sma20"] / out["sma20"].shift(5) - 1) / 5 * 100
    out["slope_sma50"] = (out["sma50"] / out["sma50"].shift(5) - 1) / 5 * 100
    out["slope_sma200"] = (out["sma200"] / out["sma200"].shift(10) - 1) / 10 * 100
    for n in (20, 50, 200):
        out[f"above_sma{n}"] = (c > out[f"sma{n}"]).where(out[f"sma{n}"].notna())
    gc = (out["sma50"] > out["sma200"]) & (out["sma50"].shift(1) <= out["sma200"].shift(1))
    out["golden_cross_days"] = days_since(gc)
    rc = (c > out["sma200"]) & (c.shift(1) <= out["sma200"].shift(1))
    out["sma200_reclaim_days"] = days_since(rc)
    rc50 = (c > out["sma50"]) & (c.shift(1) <= out["sma50"].shift(1))
    out["sma50_reclaim_days"] = days_since(rc50)

    # VOLATILITÉ
    tr = true_range(h, l, c)
    out["atr14"] = wilder(tr, 14)
    out["atr_pct"] = out["atr14"] / c * 100
    out["compression"] = out["atr_pct"] / out["atr_pct"].rolling(60, min_periods=60).mean()
    out["ext_atr"] = (c - out["sma50"]) / out["atr14"]

    # RELATIVE STRENGTH
    b = bench.reindex(df.index.union(bench.index)).ffill().reindex(df.index)
    for k, n in RS_WINDOWS.items():
        out[f"rs_{k}"] = (c / c.shift(n) - 1) - (b / b.shift(n) - 1)
    rs_line = c / b
    out["rs_line_chg10"] = rs_line / rs_line.shift(10) - 1
    out["rs_line_chg10_prev"] = out["rs_line_chg10"].shift(10)
    if local_bench is not None:
        lb = local_bench.reindex(df.index.union(local_bench.index)).ffill().reindex(df.index)
        out["rs_local_20d"] = (c / c.shift(20) - 1) - (lb / lb.shift(20) - 1)
        out["rs_local_6m"] = (c / c.shift(126) - 1) - (lb / lb.shift(126) - 1)

    # TENDANCE LONG TERME (qualité)
    lc = np.log(c)
    s126, r126 = rolling_linreg(lc, 126)
    out["trend_slope_ann"] = np.exp(s126 * 252) - 1
    out["trend_r2"] = r126

    # CONSOLIDATION / RÉSISTANCE
    out["range20_atr"] = (h.rolling(20).max() - l.rolling(20).min()) / out["atr14"]
    out["range50_atr"] = (h.rolling(50).max() - l.rolling(50).min()) / out["atr14"]
    out["consolidating"] = out["range20_atr"] <= 6.0
    out["consolidation_days"] = streak(out["consolidating"])
    out["resistance"] = h.rolling(60, min_periods=60).max().shift(1)
    out["dist_resistance"] = out["resistance"] / c - 1
    out["support"] = l.rolling(60, min_periods=60).min().shift(1)
    out["up_sessions10"] = up_day.astype(float).rolling(10).sum()

    # BREAKOUTS
    out = out.join(breakouts(out))

    # BEFORE BREAKOUT (8 conditions)
    out = out.join(before_breakout(out))

    # CONFIGURATIONS PRIX + VOLUME
    out["setup"] = setup_pattern(out)

    # ANOMALIES (z-scores sur l'historique propre du titre)
    out = out.join(zscores(out))
    return out


def breakouts(x: pd.DataFrame) -> pd.DataFrame:
    """Franchissement des plus hauts 20j / 50j / 100j / 52 semaines.

    Pour chaque niveau : séances depuis le franchissement, niveau franchi,
    et un Breakout Strength Score 0-100 (distance, volume relatif, amplitude
    de la bougie, clôture près du haut, confirmation sur plusieurs séances).
    """
    c, h, l = x["close"], x["high"], x["low"]
    rng = (h - l).replace(0, np.nan)
    clv = (c - l) / rng
    rng_atr = (h - l) / x["atr14"]
    res = {}
    strengths = []
    level_factor = {"20d": 0.7, "50d": 0.85, "100d": 0.95, "52w": 1.0}
    for k, n in BREAKOUT_WINDOWS.items():
        lvl = h.rolling(n, min_periods=n).max().shift(1)
        above = c > lvl
        cross = above & ~above.shift(1, fill_value=False)
        lvl_at = lvl.where(cross).ffill()
        ds = days_since(cross)
        holding = c > lvl_at
        confirm = np.minimum(streak(holding), ds + 1)
        dist = c / lvl_at - 1
        rv_at = x["rvol"].where(cross).ffill()
        ra_at = rng_atr.where(cross).ffill()
        clv_at = clv.where(cross).ffill()
        d_score = np.where(dist <= 0.03, lin(dist, -0.005, 0.03) * 0.4 + 0.6,
                           np.where(dist <= 0.08, 1.0, lin(dist, 0.15, 0.08) * 0.7 + 0.3))
        d_score = pd.Series(d_score, index=x.index)
        strength = 100 * (0.20 * d_score.fillna(0) + 0.30 * lin(rv_at, 1.0, 2.5).fillna(0)
                          + 0.15 * lin(ra_at, 0.8, 2.0).fillna(0) + 0.15 * lin(clv_at, 0.4, 0.9).fillna(0)
                          + 0.20 * (confirm.clip(upper=3) / 3))
        active = (ds <= 5) & holding & lvl_at.notna()
        strength = (strength * level_factor[k]).where(active, 0.0)
        res[f"brk_{k}_level"] = lvl
        res[f"brk_{k}_days"] = ds.where(lvl_at.notna())
        res[f"brk_{k}_cross_level"] = lvl_at
        res[f"brk_{k}_confirm"] = confirm
        res[f"brk_{k}_active"] = active
        res[f"brk_{k}_strength"] = strength
        strengths.append(strength)
    out = pd.DataFrame(res, index=x.index)
    out["breakout_strength"] = pd.concat(strengths, axis=1).max(axis=1)
    out["in_breakout"] = out["brk_50d_active"] | out["brk_100d_active"] | out["brk_52w_active"]
    # sortie de consolidation : cassure du plus haut 20j après une base serrée
    tight_prev = x["range20_atr"].shift(1) <= 6.0
    out["consolidation_exit"] = (out["brk_20d_days"] == 0) & tight_prev
    # sortie de range : cassure du plus haut 50j après un range 50j contenu
    range_prev = x["range50_atr"].shift(1) <= 10.0
    out["range_exit"] = (out["brk_50d_days"] == 0) & range_prev
    return out


def before_breakout(x: pd.DataFrame) -> pd.DataFrame:
    near = (x["dist_resistance"] >= 0) & (x["dist_resistance"] <= np.maximum(0.05, 2 * x["atr_pct"] / 100))
    checks = {
        "bb_consolidation": x["range20_atr"] <= 6.0,
        "bb_compression": x["compression"] < 0.9,
        "bb_vol_rising": (x["vol10_trend"] >= 1.10) | (x["vacc_score"] >= 50),
        "bb_near_resistance": near,
        "bb_sma20_50_rising": (x["slope_sma20"] > 0) & (x["slope_sma50"] > 0),
        "bb_rsi_50_70": (x["rsi14"] >= 50) & (x["rsi14"] <= 70),
        "bb_positive_sessions": x["up_sessions10"] >= 6,
        "bb_rs_improving": x["rs_line_chg10"] > 0.01,
    }
    out = pd.DataFrame({k: v.fillna(False).astype(bool) for k, v in checks.items()}, index=x.index)
    out["bb_count"] = out.sum(axis=1).astype(float)
    ok = ~x["in_breakout"].astype(bool) & out["bb_near_resistance"]
    out["before_breakout"] = ok & (out["bb_count"] >= 6)
    prox = lin(x["dist_resistance"], 0.08, 0.0)
    out["bb_score"] = (out["bb_count"] / 8 * 70 + prox * 30).where(ok & x["dist_resistance"].notna(), 0.0)
    return out


SETUP_LABELS = {
    "vol_leads_price": "Volume ↑ avant accélération du prix",
    "strong_up_strong_vol": "Prix ↑↑ + volume ↑↑",
    "flat_price_vol_surge": "Prix stable + volume ↑↑",
    "mild_down_vol_surge": "Prix légèrement baissier + volume ↑↑",
    "price_up_vol_up": "Prix ↑ + volume ↑",
    "none": "Aucune configuration",
}


def setup_pattern(x: pd.DataFrame) -> pd.Series:
    r5, vr = x["ret_5d"], x["vratio5"]
    gradual_vol = (x["vacc_score"] >= 45) | ((vr >= 1.3) & (x["vol_up_streak"] >= 3))
    conds = [
        ("vol_leads_price", gradual_vol & (r5.abs() < 0.04) & (x["ret_1m"] < 0.10)
         & (x["close"] > x["sma50"]) & (x["rvol"] < 3)),
        ("strong_up_strong_vol", (r5 >= 0.06) & (vr >= 1.8)),
        ("flat_price_vol_surge", (r5.abs() < 0.02) & (vr >= 1.8)),
        ("mild_down_vol_surge", (r5 > -0.06) & (r5 <= -0.02) & (vr >= 1.8)),
        ("price_up_vol_up", (r5 >= 0.02) & (vr >= 1.2)),
    ]
    out = pd.Series("none", index=x.index, dtype=object)
    for name, cond in reversed(conds):  # la première condition de la liste a priorité
        out = out.where(~cond.fillna(False).astype(bool), name)
    return out


def zscores(x: pd.DataFrame, n: int = 60) -> pd.DataFrame:
    def z(s: pd.Series, lag: int = 1) -> pd.Series:
        m = s.rolling(n, min_periods=n).mean().shift(lag)
        sd = s.rolling(n, min_periods=n).std().shift(lag)
        return (s - m) / sd.replace(0, np.nan)

    lv = np.log(x["volume"].replace(0, np.nan))
    lr = np.log(x["close"] / x["close"].shift(1))
    trp = true_range(x["high"], x["low"], x["close"]) / x["close"].shift(1)
    m5 = lv.rolling(5).mean()
    return pd.DataFrame({
        "z_volume": z(lv),
        "z_volume5": z(m5, lag=5),
        "z_return": z(lr),
        "z_range": z(trp),
    }, index=x.index)
