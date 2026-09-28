"""Collecte quotidienne : téléchargement yfinance → indicateurs → score → JSON.

Lancé chaque soir par GitHub Actions (.github/workflows/daily.yml).

  python scripts/collect.py            collecte normale (incrémentale si le cache existe)
  python scripts/collect.py --full     retélécharge tout l'historique (5 ans)

Règle absolue : aucune donnée de marché n'est inventée. Si un titre échoue, il
est listé dans meta.failures avec la raison, et la collecte continue. Si tout
échoue, latest.json n'est PAS écrasé : seul data/status.json signale l'échec.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import indicators as ind  # noqa: E402
import scoring as sc  # noqa: E402
import signals as sg  # noqa: E402
from signals import num  # noqa: E402
from universe import BENCHMARK, LOCAL_BENCHMARKS, UNIVERSE, all_download_tickers, meta  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PERIOD = "5y"        # amorçage : 5 ans (2 ans minimum pour SMA200/52s + profondeur de backtest)
INCREMENTAL_PERIOD = "1mo"   # mise à jour quotidienne
BACKFILL_SESSIONS = 60       # jours d'historique de score reconstruits dès le premier lancement
SERIES_BARS = 260            # ~1 an pour les graphiques de la fiche titre
SCHEMA_VERSION = 1

MARKET_CLOSE = {"US": ("America/New_York", dtime(16, 15)), "EU": ("Europe/Paris", dtime(17, 50))}

# Nombre de séances nécessaires pour chaque champ (sert à expliquer les N/A)
REQUIRED_BARS = {
    "ret_1y": 253, "high_52w": 252, "low_52w": 252, "dist_high_52w": 252, "rs_1y": 253,
    "ret_6m": 127, "rs_6m": 127, "trend_r2": 126, "sma200": 200, "slope_sma200": 210,
    "above_sma200": 200, "sma100": 100, "ret_3m": 64, "rs_60d": 61, "roc60": 61,
    "compression": 74, "resistance": 61, "vol_avg50": 50, "sma50": 50, "rvol5_50": 55,
    "z_volume": 61, "z_return": 62, "z_range": 62,
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


# ── téléchargement ──────────────────────────────────────────────────────────

def _yf_errors() -> dict:
    try:
        import yfinance.shared as shared
        return dict(getattr(shared, "_ERRORS", {}) or {})
    except Exception:
        return {}


def download(tickers: list[str], period: str, retries: int = 3) -> tuple[dict, dict]:
    """Télécharge l'OHLCV quotidien ajusté. Renvoie ({ticker: df}, {ticker: raison})."""
    import yfinance as yf

    got: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    todo = list(tickers)
    for attempt in range(1, retries + 1):
        if not todo:
            break
        log(f"yfinance : {len(todo)} titres, période {period}, tentative {attempt}/{retries}")
        try:
            raw = yf.download(todo, period=period, interval="1d", auto_adjust=True, group_by="ticker",
                              threads=True, progress=False)
        except Exception as e:  # réseau, blocage Yahoo...
            for t in todo:
                errors[t] = f"téléchargement impossible : {e}"
            raw = None
        yerr = _yf_errors()
        still = []
        for t in todo:
            df = None
            if raw is not None and not raw.empty:
                try:
                    sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                    sub = sub.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
                    sub = sub.dropna(subset=["close"])
                    if len(sub):
                        df = sub
                except KeyError:
                    df = None
            if df is not None:
                idx = pd.to_datetime(df.index)
                if idx.tz is not None:  # garde la date locale du marché (Paris, New York)
                    idx = idx.tz_localize(None)
                df.index = idx.normalize()
                df = df[~df.index.duplicated(keep="last")].sort_index()
                df["volume"] = df["volume"].fillna(0)
                got[t] = df
                errors.pop(t, None)
            else:
                errors[t] = str(yerr.get(t) or errors.get(t) or "aucune donnée renvoyée par Yahoo (délisté, suspendu ou ticker invalide ?)")
                still.append(t)
        todo = still
        if todo and attempt < retries:
            time.sleep(5 * attempt)
    return got, errors


def load_cache(path: Path) -> dict[str, pd.DataFrame]:
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    out = {}
    for t, g in df.groupby("ticker"):
        g = g.drop(columns="ticker").set_index("date").sort_index()
        g.index = pd.to_datetime(g.index)
        out[t] = g
    return out


def save_cache(path: Path, data: dict[str, pd.DataFrame]) -> None:
    frames = []
    for t, df in data.items():
        f = df[["open", "high", "low", "close", "volume"]].copy()
        f["ticker"] = t
        f.index.name = "date"
        frames.append(f.reset_index())
    if frames:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.concat(frames, ignore_index=True).to_parquet(path, index=False)


def merge_incremental(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame | None:
    """Colle les nouvelles séances au cache. None si un ajustement (split, dividende)
    a modifié l'historique : il faut alors retélécharger ce titre en entier."""
    common = old.index.intersection(new.index)
    check = common[:-2] if len(common) > 2 else common
    if len(check):
        diff = (old.loc[check, "close"] / new.loc[check, "close"] - 1).abs().max()
        if diff > 0.002:  # un dividende ou un split a réajusté l'historique chez Yahoo
            return None
    if len(new) and len(old) and new.index[0] > old.index[-1] + pd.Timedelta(days=10):
        return None  # trou dans l'historique
    merged = pd.concat([old[old.index < new.index[0]], new])
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def drop_open_session(df: pd.DataFrame, market: str, now: datetime) -> tuple[pd.DataFrame, bool]:
    """Retire la séance du jour si le marché n'est pas encore fermé (volume partiel)."""
    tz, close_t = MARKET_CLOSE[market]
    local = now.astimezone(ZoneInfo(tz))
    if len(df) and df.index[-1].date() == local.date() and local.time() < close_t:
        return df.iloc[:-1], True
    return df, False


def update_prices(cache_path: Path, full: bool, offline: bool) -> tuple[dict, dict]:
    tickers = all_download_tickers()
    cache = {} if full else load_cache(cache_path)
    errors: dict[str, str] = {}
    if offline:
        log("mode hors ligne : utilisation du cache uniquement")
        return {t: cache[t] for t in tickers if t in cache}, {t: "absent du cache (mode hors ligne)" for t in tickers if t not in cache}

    need_full = [t for t in tickers if t not in cache]
    incr = [t for t in tickers if t in cache]
    data = dict(cache)
    if incr:
        new, err = download(incr, INCREMENTAL_PERIOD)
        for t in incr:
            if t not in new:
                errors[t] = err.get(t, "échec du téléchargement")
                continue
            merged = merge_incremental(cache[t], new[t])
            if merged is None:
                log(f"{t} : historique ajusté (split/dividende) ou trou → retéléchargement complet")
                need_full.append(t)
            else:
                data[t] = merged
    if need_full:
        new, err = download(need_full, HISTORY_PERIOD)
        for t in need_full:
            if t in new:
                data[t] = new[t]
                errors.pop(t, None)
            else:
                errors[t] = err.get(t, "échec du téléchargement")
    for t in list(errors):
        if t in data:  # données en cache mais pas de mise à jour aujourd'hui
            errors[t] = f"mise à jour échouée, dernières données en cache du {data[t].index[-1]:%Y-%m-%d} : {errors[t]}"
    return {t: data[t] for t in tickers if t in data}, errors


# ── helpers JSON ────────────────────────────────────────────────────────────

def clean(o):
    """Convertit numpy/pandas en types JSON ; NaN → None (jamais de valeur inventée)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(clean(obj), f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        else:
            json.dump(clean(obj), f, ensure_ascii=False, allow_nan=False, indent=1)
    tmp.replace(path)


def bval(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return bool(v)


def ival(v):
    f = num(v)
    return None if f is None else int(f)


# ── assemblage d'un titre ───────────────────────────────────────────────────

def na_reasons(stock: dict, bars: int) -> dict:
    out = {}

    def walk(d, prefix=""):
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                walk(v, key + ".")
            elif v is None:
                leaf = k.replace("chg_", "ret_")
                req = REQUIRED_BARS.get(leaf) or REQUIRED_BARS.get(k)
                if req and bars < req:
                    out[key] = f"{bars} séances d'historique, {req} nécessaires"
                else:
                    out[key] = "non calculable à cette date (donnée source manquante)"
    walk({k: stock[k] for k in ("price", "volume", "momentum", "trend", "volatility", "rs")})
    # absence d'événement récent ou champ non applicable : pas une donnée manquante
    for k in ("trend.golden_cross_days_ago", "trend.sma200_reclaim_days_ago"):
        out.pop(k, None)
    if stock["market"] != "EU":
        for k in ("rs.local_benchmark", "rs.rs_local_20d", "rs.rs_local_6m"):
            out.pop(k, None)
    return out


def build_stock(t: str, df: pd.DataFrame, x: pd.DataFrame, s: pd.DataFrame, cfg: dict,
                dropped_open: bool) -> dict:
    m = meta(t)
    row, srow = x.iloc[-1], s.iloc[-1]
    cur = m["currency"]
    zthr = cfg["anomalies"]["zscore_threshold"]

    hists = {}
    for p in cfg["profiles"]:
        ser = s[f"score_{p}"]
        h = ser.iloc[-11:]
        vals = [num(v, 2) for v in h]
        last = vals[-1]

        def d(k):
            return num(last - vals[-1 - k], 2) if last is not None and len(vals) > k and vals[-1 - k] is not None else None
        hists[p] = {"total": last, "delta": {"d1": d(1), "d3": d(3), "d5": d(5), "d10": d(10)},
                    "hist": vals, "hist_dates": [f"{i:%Y-%m-%d}" for i in h.index]}

    stock = {
        **m,
        "last_bar_date": f"{x.index[-1]:%Y-%m-%d}",
        "bars_available": int(len(df)),
        "open_session_dropped": dropped_open,
        "price": {
            "close": num(row["close"]),
            **{f"chg_{k}": num(row[f"ret_{k}"]) for k in ind.RET_WINDOWS},
            "high_52w": num(row["high_52w"]), "low_52w": num(row["low_52w"]),
            "dist_high_52w": num(row["dist_high_52w"]),
        },
        "volume": {
            "today": num(row["volume"], 0), "avg20": num(row["vol_avg20"], 0), "avg50": num(row["vol_avg50"], 0),
            "rvol": num(row["rvol"], 3), "rvol5_50": num(row["rvol5_50"], 3),
            "updown_vol20": num(row["updown_vol20"], 3),
            "accel": {
                "slope5_pct": num(row["vacc_slope5"], 2), "r2_5": num(row["vacc_r2_5"], 3),
                "slope10_pct": num(row["vacc_slope10"], 2), "r2_10": num(row["vacc_r2_10"], 3),
                "up_streak": ival(row["vol_up_streak"]), "score": num(row["vacc_score"], 1),
            },
            "last10": [num(v, 0) for v in x["volume"].iloc[-10:]],
        },
        "momentum": {
            "rsi14": num(row["rsi14"], 2), "rsi7": num(row["rsi7"], 2),
            "macd": num(row["macd"]), "macd_signal": num(row["macd_signal"]), "macd_hist": num(row["macd_hist"]),
            "roc20": num(row["roc20"]), "roc60": num(row["roc60"]),
            "slope_sma20": num(row["slope_sma20"], 3), "slope_sma50": num(row["slope_sma50"], 3),
            "slope_sma200": num(row["slope_sma200"], 3),
        },
        "trend": {
            **{f"sma{n}": num(row[f"sma{n}"]) for n in (20, 50, 100, 200)},
            "ema20": num(row["ema20"]), "ema50": num(row["ema50"]),
            **{f"above_sma{n}": bval(row[f"above_sma{n}"]) for n in (20, 50, 200)},
            "golden_cross_days_ago": ival(row["golden_cross_days"]) if num(row["golden_cross_days"]) is not None and row["golden_cross_days"] <= 120 else None,
            "sma200_reclaim_days_ago": ival(row["sma200_reclaim_days"]) if num(row["sma200_reclaim_days"]) is not None and row["sma200_reclaim_days"] <= 60 else None,
            "trend_r2": num(row["trend_r2"], 3), "trend_slope_ann": num(row["trend_slope_ann"]),
        },
        "volatility": {
            "atr14": num(row["atr14"]), "atr_pct": num(row["atr_pct"], 3),
            "compression": num(row["compression"], 3), "ext_atr": num(row["ext_atr"], 2),
        },
        "rs": {
            "benchmark": BENCHMARK,
            **{f"rs_{k}": num(row[f"rs_{k}"]) for k in ind.RS_WINDOWS},
            "rs_line_chg10": num(row["rs_line_chg10"]),
            "rs_trend": ("improving" if (row["rs_line_chg10"] or 0) > 0.01 else
                         "deteriorating" if (row["rs_line_chg10"] or 0) < -0.01 else "flat")
            if num(row["rs_line_chg10"]) is not None else None,
            "local_benchmark": LOCAL_BENCHMARKS.get(m["market"]),
            "rs_local_20d": num(row.get("rs_local_20d")), "rs_local_6m": num(row.get("rs_local_6m")),
        },
        "setup": {
            "pattern": row["setup"],
            "pattern_label": ind.SETUP_LABELS.get(row["setup"], ""),
            "resistance": num(row["resistance"]), "dist_resistance": num(row["dist_resistance"]),
            "support": num(row["support"]),
            "consolidation_days": ival(row["consolidation_days"]),
            "range20_atr": num(row["range20_atr"], 2),
            "up_sessions10": ival(row["up_sessions10"]),
            "before_breakout": bool(row["before_breakout"]),
            "bb_count": ival(row["bb_count"]),
            "bb_score": num(row["bb_score"], 1),
            "in_breakout": bool(row["in_breakout"]),
            "before_breakout_checks": {k[3:]: bool(row[k]) for k in (
                "bb_consolidation", "bb_compression", "bb_vol_rising", "bb_near_resistance",
                "bb_sma20_50_rising", "bb_rsi_50_70", "bb_positive_sessions", "bb_rs_improving")},
        },
        "score": {
            "components": {k: num(srow[f"c_{k}"], 1) for k in sc.COMPONENT_LABELS},
            "early_rs_bonus": bool(srow["c_early_rs"]),
            "overextension_penalty": num(srow["penalty"], 3),
            "setup_bonus": num(srow["setup_bonus"], 1),
            "profiles": hists,
            "reasons": sg.reasons(row, srow, cur),
        },
        "breakouts": sg.breakout_list(row),
        "breakout_levels": {k: num(row[f"brk_{k}_level"]) for k in ind.BREAKOUT_WINDOWS},
        "breakout_strength": num(row["breakout_strength"], 1),
        "anomalies": sg.anomaly_list(x, zthr),
        "zscores": {k: num(row[k], 2) for k in ("z_volume", "z_volume5", "z_return", "z_range")},
        "what_changed": sg.what_changed(x, s["score_standard"]),
    }
    stock["similarity"] = sg.similarity(row, srow, hists["standard"]["hist"], cur)
    stock["na"] = na_reasons(stock, len(df))
    return stock


def series_payload(t: str, x: pd.DataFrame, s: pd.DataFrame, stock: dict) -> dict:
    d = x.iloc[-SERIES_BARS:]
    ss = s.iloc[-SERIES_BARS:]

    def arr(col, nd=4, frame=d):
        return [num(v, nd) for v in frame[col]]
    return {
        "ticker": t,
        "currency": stock["currency"],
        "dates": [f"{i:%Y-%m-%d}" for i in d.index],
        "open": arr("open"), "high": arr("high"), "low": arr("low"), "close": arr("close"),
        "volume": arr("volume", 0),
        "sma20": arr("sma20"), "sma50": arr("sma50"), "sma200": arr("sma200"),
        "rsi14": arr("rsi14", 2),
        "macd": arr("macd"), "macd_signal": arr("macd_signal"), "macd_hist": arr("macd_hist"),
        "scores": {p: arr(f"score_{p}", 1, ss) for p in ("standard", "swing", "long_term") if f"score_{p}" in ss},
        "levels": sg.sr_levels(x),
        "breakout_levels": stock["breakout_levels"],
        "resistance_60d": stock["setup"]["resistance"],
    }


# ── sections, secteurs, alertes ─────────────────────────────────────────────

def _key(v, default=-math.inf):
    return default if v is None else v


def score_matrix(results: dict, cal: pd.DatetimeIndex, profile: str) -> pd.DataFrame:
    cols = {}
    for t, (_, x, s) in results.items():
        ser = s[f"score_{profile}"]
        cols[t] = ser.reindex(cal.union(ser.index)).ffill(limit=5).reindex(cal)
    return pd.DataFrame(cols, index=cal)


def new_names(mat: pd.DataFrame, cfg: dict) -> tuple[list[dict], int]:
    sec = cfg["sections"]
    n_univ = mat.shape[1]
    top_n = int(min(sec["new_names_top_n"], max(10, round(n_univ * sec["new_names_min_universe_share"]))))
    win, look = sec["new_names_window"], sec["new_names_lookback"]
    if len(mat) < win + look + 1:
        return [], top_n
    ranks = mat.rank(axis=1, ascending=False, method="first")
    in_top = ranks <= top_n
    out = []
    today = in_top.iloc[-1]
    for t in mat.columns[today.to_numpy()]:
        col = in_top[t].to_numpy()
        # séance d'entrée = début de la série actuelle de présence dans le top
        k = len(col) - 1
        while k > 0 and col[k - 1]:
            k -= 1
        days_in = len(col) - 1 - k
        if days_in >= win:
            continue
        before = col[max(0, k - look):k]
        if len(before) and not before.any():
            out.append({"ticker": t, "entered_days_ago": int(days_in), "rank": int(ranks[t].iloc[-1])})
    out.sort(key=lambda r: (r["entered_days_ago"], r["rank"]))
    return out, top_n


def build_sections(stocks: dict, cfg: dict, new_by_profile: dict) -> dict:
    n = cfg["sections"]["top_n"]
    out = {}
    for p in cfg["profiles"]:
        S = list(stocks.values())
        by_score = sorted(S, key=lambda st: -_key(st["score"]["profiles"][p]["total"]))
        vol_key = lambda st: max(_key(st["zscores"]["z_volume"]), _key(st["zscores"]["z_volume5"]))  # noqa: E731
        surge = [st for st in sorted(S, key=lambda st: -vol_key(st)) if vol_key(st) > -math.inf]
        bb = [st for st in S if st["setup"]["before_breakout_checks"]["near_resistance"]
              and not st["setup"]["in_breakout"] and (st["setup"]["bb_count"] or 0) >= 5]
        bb.sort(key=lambda st: (-int(st["setup"]["before_breakout"]), -_key(st["setup"]["bb_score"]),
                                -_key(st["score"]["profiles"][p]["total"])))
        acc = [st for st in S if st["score"]["profiles"][p]["delta"]["d5"] is not None]
        acc.sort(key=lambda st: (-st["score"]["profiles"][p]["delta"]["d5"],
                                 -_key(st["score"]["profiles"][p]["delta"]["d3"])))
        out[p] = {
            "top_momentum": [st["ticker"] for st in by_score[:n]],
            "volume_surge": [st["ticker"] for st in surge[:n]],
            "before_breakout": [st["ticker"] for st in bb[:n]],
            "momentum_acceleration": [st["ticker"] for st in acc[:n]],
            "new_names": new_by_profile[p][0],
            "new_names_top_n": new_by_profile[p][1],
        }
    S = list(stocks.values())
    out["next_mu"] = [st["ticker"] for st in sorted(S, key=lambda st: -st["similarity"]["A"]["score"])[:10]]
    out["next_sndk"] = [st["ticker"] for st in sorted(S, key=lambda st: -st["similarity"]["B"]["score"])[:10]]
    return out


def build_sectors(stocks: dict, cfg: dict) -> list[dict]:
    by: dict[str, list] = {}
    for st in stocks.values():
        by.setdefault(st["sector"], []).append(st)

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return float(np.mean(vals)) if vals else None
    out = []
    for sec, L in by.items():
        rec = {
            "sector": sec, "count": len(L), "tickers": [s["ticker"] for s in L],
            "chg_1d": avg([s["price"]["chg_1d"] for s in L]),
            "chg_5d": avg([s["price"]["chg_5d"] for s in L]),
            "chg_1m": avg([s["price"]["chg_1m"] for s in L]),
            "rvol": avg([s["volume"]["rvol"] for s in L]),
            "score": {p: avg([s["score"]["profiles"][p]["total"] for s in L]) for p in cfg["profiles"]},
            "score_d5": {p: avg([s["score"]["profiles"][p]["delta"]["d5"] for s in L]) for p in cfg["profiles"]},
        }
        out.append(rec)
    out.sort(key=lambda r: -_key(r["score"].get(cfg["default_profile"])))
    return out


def build_alerts(stocks: dict, cfg: dict) -> list[dict]:
    a = cfg["alerts"]
    p = cfg.get("alerts_profile", cfg["default_profile"])
    out = []
    for st in stocks.values():
        sp = st["score"]["profiles"][p]
        score, hist = sp["total"], sp["hist"]
        prev = hist[-2] if len(hist) >= 2 else None
        rvol = st["volume"]["rvol"]
        vacc = st["volume"]["accel"]["score"]
        brk_today = [b for b in st["breakouts"] if b["days_ago"] == 0]
        triggers = []
        if score is not None and score > a["score_above"] and (prev is None or prev <= a["score_above"]):
            triggers.append("score")
        if rvol is not None and rvol > a["rvol_above"]:
            triggers.append("rvol")
        if brk_today and rvol is not None and rvol >= a["breakout_min_rvol"]:
            triggers.append("breakout_volume")
        if vacc is not None and vacc >= a["volume_accel_above"]:
            triggers.append("volume_accel")
        if not triggers:
            continue
        why = []
        streak_v = st["volume"]["accel"]["up_streak"] or 0
        if "volume_accel" in triggers or streak_v >= 3:
            why.append(f"volume accélère depuis {streak_v} séance(s) (accel {vacc:.0f}/100)" if streak_v >= 2
                       else f"volume en accélération (accel {vacc:.0f}/100)")
        if "rvol" in triggers:
            why.append(f"volume du jour {rvol:.1f}× la moyenne 20j")
        if brk_today:
            b = brk_today[0]
            why.append(f"breakout du {b['label']} à {b['level']:.2f}")
        elif st["setup"]["dist_resistance"] is not None and st["setup"]["dist_resistance"] >= 0:
            why.append(f"breakout à {st['setup']['dist_resistance'] * 100:.1f} % (résistance {st['setup']['resistance']:.2f})")
        if "score" in triggers:
            why.append(f"score {prev:.0f} → {score:.0f}" if prev is not None else f"score {score:.0f}")
        out.append({
            "ticker": st["ticker"], "name": st["name"], "currency": st["currency"],
            "price": st["price"]["close"], "chg_1d": st["price"]["chg_1d"], "rvol": rvol,
            "score": score, "breakout": bool(st["breakouts"]), "triggers": triggers,
            "reason": ", ".join(why),
        })
    out.sort(key=lambda r: -_key(r["score"]))
    return out


# ── historique ──────────────────────────────────────────────────────────────

def snapshot_at(results: dict, date: pd.Timestamp, profiles) -> dict:
    snap = {}
    for t, (_, x, s) in results.items():
        xi = x.loc[:date]
        if not len(xi) or (date - xi.index[-1]).days > 5:
            continue
        si = s.loc[xi.index[-1]]
        r = xi.iloc[-1]
        snap[t] = {
            "close": num(r["close"]), "chg_1d": num(r["ret_1d"]), "rvol": num(r["rvol"], 3),
            **{p: num(si[f"score_{p}"], 2) for p in profiles},
            "price": num(si["c_price_momentum"], 1), "vol": num(si["c_volume_momentum"], 1),
            "brk": num(si["c_breakout_potential"], 1),
        }
    return snap


def write_history(results: dict, cal: pd.DatetimeIndex, cfg: dict, hist_dir: Path, now: datetime) -> dict:
    hist_dir.mkdir(parents=True, exist_ok=True)
    profiles = list(cfg["profiles"])
    today = cal[-1]
    written = 0
    for d in cal[-BACKFILL_SESSIONS:]:
        path = hist_dir / f"{d:%Y-%m-%d}.json"
        is_today = d == today
        if path.exists() and not is_today:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if not existing.get("backfilled", False):
                    continue  # un snapshot enregistré en direct n'est jamais réécrit
            except Exception:
                pass
        write_json(path, {
            "date": f"{d:%Y-%m-%d}",
            "backfilled": not is_today,
            "generated_at_utc": now.isoformat(timespec="seconds"),
            "note": "recalculé depuis prices.parquet avec les données disponibles à cette date" if not is_today else "snapshot de la collecte du jour",
            "stocks": snapshot_at(results, d, profiles),
        }, compact=True)
        written += 1
    files = sorted(hist_dir.glob("*.json"))
    live = 0
    for f in files:
        try:
            if not json.loads(f.read_text(encoding="utf-8")).get("backfilled", False):
                live += 1
        except Exception:
            pass
    return {"files": len(files), "live": live, "backfilled": len(files) - live, "written": written}


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="retélécharge tout l'historique")
    ap.add_argument("--offline", action="store_true", help="n'utilise que le cache local (tests)")
    ap.add_argument("--data-dir", default=str(ROOT / "data"), help="dossier de sortie")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    status_path = data_dir / "status.json"
    try:
        cfg = sc.load_config()
        prices, errors = update_prices(data_dir / "prices.parquet", args.full, args.offline)
        if BENCHMARK not in prices:
            raise RuntimeError(f"benchmark {BENCHMARK} indisponible : {errors.get(BENCHMARK, 'inconnu')}")
        save_cache(data_dir / "prices.parquet", prices)

        bench = prices[BENCHMARK]["close"]
        local = {m: prices[b]["close"] for m, b in LOCAL_BENCHMARKS.items() if b in prices}
        results, stocks, failures, dropped = {}, {}, [], []
        for t in UNIVERSE:
            if t not in prices:
                failures.append({"ticker": t, "stage": "download", "reason": errors.get(t, "aucune donnée")})
                continue
            try:
                m = meta(t)
                df, was_open = drop_open_session(prices[t], m["market"], now)
                if was_open:
                    dropped.append(t)
                if len(df) < 30:
                    failures.append({"ticker": t, "stage": "indicators", "reason": f"seulement {len(df)} séances d'historique (30 minimum)"})
                    continue
                x = ind.compute(df, bench, local.get(m["market"]))
                s = sc.score_frame(x, cfg)
                results[t] = (df, x, s)
                stocks[t] = build_stock(t, df, x, s, cfg, was_open)
                if t in errors:
                    failures.append({"ticker": t, "stage": "download", "reason": errors[t]})
            except Exception as e:
                traceback.print_exc()
                failures.append({"ticker": t, "stage": "indicators", "reason": f"{type(e).__name__}: {e}"})

        if not stocks:
            raise RuntimeError("aucun titre n'a pu être calculé")

        bench_df, _ = drop_open_session(prices[BENCHMARK], "US", now)
        cal = bench_df.index
        new_by_profile = {p: new_names(score_matrix(results, cal[-40:], p), cfg) for p in cfg["profiles"]}
        sections = build_sections(stocks, cfg, new_by_profile)
        sectors = build_sectors(stocks, cfg)
        alerts = build_alerts(stocks, cfg)
        hist_info = write_history(results, cal, cfg, data_dir / "history", now)

        for t, (df, x, s) in results.items():
            write_json(data_dir / "series" / f"{t.replace('^', '_')}.json", series_payload(t, x, s, stocks[t]), compact=True)

        def bench_info(sym):
            if sym not in prices:
                return None
            c = prices[sym]["close"]
            return {"close": num(c.iloc[-1]), "date": f"{c.index[-1]:%Y-%m-%d}",
                    "chg_1d": num(c.iloc[-1] / c.iloc[-2] - 1), "chg_5d": num(c.iloc[-1] / c.iloc[-6] - 1),
                    "chg_20d": num(c.iloc[-1] / c.iloc[-21] - 1)}

        market_date = {mk: max((st["last_bar_date"] for st in stocks.values() if st["market"] == mk), default=None)
                       for mk in ("US", "EU")}
        latest = {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "generated_at_utc": now.isoformat(timespec="seconds"),
                "last_success_utc": now.isoformat(timespec="seconds"),
                "market_date": market_date,
                "source": "yfinance (Yahoo Finance), cours quotidiens ajustés splits/dividendes",
                "profile_default": cfg["default_profile"],
                "profiles": {k: {"label": v["label"], "weights": v["weights"]} for k, v in cfg["profiles"].items()},
                "component_labels": sc.COMPONENT_LABELS,
                "config": {k: cfg[k] for k in ("overextension", "setup_bonus", "alerts", "anomalies", "sections")},
                "history": hist_info,
                "score_history_source": "recalculé chaque jour depuis prices.parquet, point-in-time (aucune donnée postérieure à chaque date)",
                "universe_count": len(UNIVERSE),
                "ok_count": len(stocks),
                "failures": failures,
                "open_session_dropped": dropped,
                "benchmarks": {BENCHMARK: bench_info(BENCHMARK),
                               **{b: bench_info(b) for b in LOCAL_BENCHMARKS.values()}},
            },
            "stocks": sorted(stocks.values(), key=lambda st: -_key(st["score"]["profiles"][cfg["default_profile"]]["total"])),
            "sections": sections,
            "sectors": sectors,
            "alerts": alerts,
        }
        write_json(data_dir / "latest.json", latest, compact=True)
        write_json(data_dir / "alerts.json", {
            "generated_at_utc": now.isoformat(timespec="seconds"), "market_date": market_date,
            "profile": cfg.get("alerts_profile"), "rules": cfg["alerts"], "alerts": alerts})
        write_json(status_path, {"last_attempt_utc": now.isoformat(timespec="seconds"), "ok": True,
                                 "ok_count": len(stocks), "failed_count": len(failures),
                                 "message": f"{len(stocks)}/{len(UNIVERSE)} titres calculés"})
        log(f"OK : {len(stocks)}/{len(UNIVERSE)} titres, {len(alerts)} alertes, {len(failures)} échecs")
        for f in failures:
            log(f"  échec {f['ticker']} ({f['stage']}) : {f['reason']}")
        return 0
    except Exception as e:
        traceback.print_exc()
        prev_success = None
        try:
            prev_success = json.loads((data_dir / "latest.json").read_text(encoding="utf-8"))["meta"]["last_success_utc"]
        except Exception:
            pass
        write_json(status_path, {"last_attempt_utc": now.isoformat(timespec="seconds"), "ok": False,
                                 "message": f"{type(e).__name__}: {e}", "last_success_utc": prev_success})
        log(f"ÉCHEC de la collecte : {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
