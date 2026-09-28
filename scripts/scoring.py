"""Momentum Score 0-100, sous-scores et profils (Standard / Swing / Long terme).

Tout est calculé en séries temporelles : le score à la date T n'utilise que
les indicateurs connus à T. collect.py lit la dernière ligne, backtest.py
rejoue toutes les lignes avec des pondérations éventuellement différentes.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from indicators import lin, wavg

ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_PATH = ROOT / "config" / "weights.json"

COMPONENT_LABELS = {
    "volume_momentum": "Volume Momentum",
    "price_momentum": "Price Momentum",
    "relative_strength": "Relative Strength",
    "breakout_potential": "Breakout Potential",
    "technical_trend": "Technical Trend",
    "acceleration": "Acceleration",
    "volatility_compression": "Volatility Compression",
    "rs_long": "RS long terme (6m / 1an)",
    "trend_quality": "Qualité de tendance",
    "volatility_risk": "Volatilité maîtrisée",
}


def load_config(path: Path | str | None = None) -> dict:
    with open(path or WEIGHTS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    for name, p in cfg["profiles"].items():
        total = sum(p["weights"].values())
        if abs(total - 100) > 0.01:
            raise ValueError(f"profil {name} : la somme des poids vaut {total}, elle doit valoir 100")
        unknown = set(p["weights"]) - set(COMPONENT_LABELS)
        if unknown:
            raise ValueError(f"profil {name} : composantes inconnues {sorted(unknown)}")
    return cfg


def _b(s: pd.Series, ref: pd.Series | None = None) -> pd.Series:
    """Booléen -> 0/1, NaN là où la donnée de référence manque."""
    out = s.astype(float)
    if ref is not None:
        out = out.where(ref.notna())
    return out


def components(x: pd.DataFrame) -> pd.DataFrame:
    c = pd.DataFrame(index=x.index)

    c["volume_momentum"] = wavg([
        (0.40, x["vacc_score"]),
        (0.20, lin(x["rvol"], 1.0, 3.0) * 100),
        (0.20, lin(x["rvol5_50"], 1.0, 2.0) * 100),
        (0.20, lin(x["updown_vol20"], 0.8, 2.0) * 100),
    ], min_share=0.6)

    c["price_momentum"] = wavg([
        (0.25, lin(x["ret_5d"], -0.01, 0.06) * 100),
        (0.35, lin(x["ret_1m"], -0.02, 0.15) * 100),
        (0.25, lin(x["ret_3m"], 0.0, 0.30) * 100),
        (0.15, _b(x["macd_hist"] > 0, x["macd_hist"]) * 100),
    ], min_share=0.6)

    rs = wavg([
        (0.25, lin(x["rs_5d"], -0.02, 0.05)),
        (0.35, lin(x["rs_20d"], -0.03, 0.10)),
        (0.20, lin(x["rs_60d"], -0.05, 0.20)),
        (0.20, lin(x["rs_6m"], -0.10, 0.30)),
    ], min_share=0.6) * 100
    # Bonus : surperformance qui démarre AVANT le breakout (pas encore sur ses plus hauts)
    early_rs = (x["rs_line_chg10"] > 0.01) & (x["rs_20d"] > 0) & (x["dist_high_52w"].fillna(-1) < -0.03)
    c["early_rs"] = early_rs.astype(bool)
    c["relative_strength"] = (rs + np.where(early_rs, 15.0, 0.0)).clip(upper=100)

    c["breakout_potential"] = np.maximum(x["bb_score"].fillna(0), 0.8 * x["breakout_strength"].fillna(0))
    c["breakout_potential"] = c["breakout_potential"].where(x["resistance"].notna())

    c["technical_trend"] = wavg([
        (20, _b(x["above_sma20"] == True, x["sma20"])),  # noqa: E712
        (20, _b(x["above_sma50"] == True, x["sma50"])),  # noqa: E712
        (20, _b(x["above_sma200"] == True, x["sma200"])),  # noqa: E712
        (15, _b(x["slope_sma50"] > 0, x["slope_sma50"])),
        (15, _b(x["slope_sma200"] > 0, x["slope_sma200"])),
        (10, _b(x["sma50"] > x["sma200"], x["sma200"])),
    ], min_share=0.4) * 100

    hist = x["macd_hist"]
    hist_up = _b((hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2)), hist.shift(2))
    c["acceleration"] = wavg([
        (0.4, lin(x["ret_5d"] - x["ret_1m"] / 4.2, -0.01, 0.03)),
        (0.3, lin(x["roc20"] - x["roc20"].shift(10), -0.05, 0.10)),
        (0.3, hist_up),
    ], min_share=0.6) * 100

    trend_ok = np.where(x["close"] > x["sma50"], 1.0, 0.5)
    c["volatility_compression"] = lin(x["compression"], 1.1, 0.6) * 100 * trend_ok

    c["rs_long"] = wavg([
        (0.5, lin(x["rs_6m"], -0.10, 0.30)),
        (0.5, lin(x["rs_1y"], -0.15, 0.50)),
    ], min_share=0.5) * 100
    c["trend_quality"] = x["trend_r2"] * lin(x["trend_slope_ann"], 0.0, 0.5) * 100
    c["volatility_risk"] = lin(x["atr_pct"], 6.0, 2.0) * 100
    return c


def overextension(x: pd.DataFrame, cfg: dict) -> pd.Series:
    o = cfg["overextension"]
    pen = (
        (x["rsi14"] - o["rsi_start"]).clip(lower=0).fillna(0) * o["rsi_per_point"]
        + (x["ext_atr"] - o["atr_ext_start"]).clip(lower=0).fillna(0) * o["atr_ext_per_atr"]
        + (x["ret_1y"] - o["ret_1y_start"]).clip(lower=0).fillna(0) * o["ret_1y_per_100pct"]
    )
    return (1 - pen).clip(lower=o["floor"], upper=1.0)


def setup_bonus(x: pd.DataFrame, cfg: dict) -> pd.Series:
    table = cfg.get("setup_bonus", {})
    return x["setup"].map(lambda s: float(table.get(s, 0) or 0)).astype(float)


def profile_score(comp: pd.DataFrame, pen: pd.Series, bonus: pd.Series, weights: dict) -> pd.Series:
    base = wavg([(w, comp[k]) for k, w in weights.items()], min_share=0.7)
    return ((base + bonus) * pen).clip(0, 100)


def score_frame(x: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Composantes + pénalité + bonus + score de chaque profil, pour chaque séance."""
    comp = components(x)
    pen = overextension(x, cfg)
    bonus = setup_bonus(x, cfg)
    out = comp.add_prefix("c_")
    out["penalty"] = pen
    out["setup_bonus"] = bonus
    for name, p in cfg["profiles"].items():
        out[f"score_{name}"] = profile_score(comp, pen, bonus, p["weights"])
    return out
