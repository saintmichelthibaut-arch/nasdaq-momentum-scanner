"""Backtest : « si j'avais pris chaque semaine les 10 meilleurs scores,
quelle performance à +5, +20 et +60 séances ? »

  python scripts/backtest.py
  python scripts/backtest.py --weights config/autre.json --top 5 --thresholds 0,60,70,80

Anti look-ahead :
  - les scores sont RECALCULÉS depuis prices.parquet (pas lus dans l'historique),
    donc on peut tester d'autres pondérations rétroactivement ;
  - le score à la date T n'utilise que les séances <= T ;
  - le signal est pris à la clôture de T, l'achat se fait à l'OUVERTURE de T+1,
    la vente à la clôture de T+h.

Biais connu et NON corrigé : l'univers est la liste actuelle (biais du
survivant). Des titres choisis parce qu'ils ont bien monté rendent le backtest
optimiste. C'est écrit dans les résultats.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import indicators as ind  # noqa: E402
import scoring as sc  # noqa: E402
from collect import HISTORY_PERIOD, download, load_cache, log, save_cache, write_json  # noqa: E402
from universe import BENCHMARK, LOCAL_BENCHMARKS, UNIVERSE, all_download_tickers, market_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WARMUP = 252
HIST_BINS = [-1.0, -0.3, -0.2, -0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 10.0]
HIST_LABELS = ["< -30 %", "-30/-20", "-20/-15", "-15/-10", "-10/-5", "-5/0", "0/+5", "+5/+10",
               "+10/+15", "+15/+20", "+20/+30", "+30/+50", "> +50 %"]


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return float("nan")
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1).min())


def stats(rets: np.ndarray, bench: np.ndarray, mae: np.ndarray) -> dict:
    if len(rets) == 0:
        return {"n": 0}
    q = np.percentile(rets, [5, 25, 50, 75, 95])
    hist, _ = np.histogram(rets, bins=HIST_BINS)
    return {
        "n": int(len(rets)),
        "mean": float(rets.mean()), "median": float(np.median(rets)),
        "hit_rate": float((rets > 0).mean()),
        "beat_qqq_rate": float((rets > bench).mean()),
        "std": float(rets.std(ddof=1)) if len(rets) > 1 else None,
        "qqq_mean": float(bench.mean()), "excess_mean": float((rets - bench).mean()),
        "best": float(rets.max()), "worst": float(rets.min()),
        "mae_mean": float(mae.mean()), "mae_worst": float(mae.min()),
        "percentiles": {"p5": q[0], "p25": q[1], "p50": q[2], "p75": q[3], "p95": q[4]},
        "distribution": {lab: int(n) for lab, n in zip(HIST_LABELS, hist)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=None, help="fichier de pondérations (défaut config/weights.json)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--thresholds", default="0,50,60,70,80", help="seuils de score testés (0 = pas de seuil)")
    ap.add_argument("--horizons", default="5,20,60")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    cfg = sc.load_config(args.weights)
    thresholds = [float(t) for t in args.thresholds.split(",")]
    horizons = [int(h) for h in args.horizons.split(",")]

    prices = load_cache(data_dir / "prices.parquet")
    if BENCHMARK not in prices:
        log("cache absent : téléchargement de l'historique")
        prices, err = download(all_download_tickers(), HISTORY_PERIOD)
        if BENCHMARK not in prices:
            log(f"impossible de récupérer {BENCHMARK} : {err.get(BENCHMARK)}")
            return 1
        save_cache(data_dir / "prices.parquet", prices)

    bench = prices[BENCHMARK]
    local = {m: prices[b]["close"] for m, b in LOCAL_BENCHMARKS.items() if b in prices}
    frames = {}
    for t in UNIVERSE:
        if t not in prices or len(prices[t]) < 60:
            continue
        df = prices[t]
        x = ind.compute(df, bench["close"], local.get(market_of(t)))
        s = sc.score_frame(x, cfg)
        frames[t] = {"idx": df.index, "open": df["open"].to_numpy(), "close": df["close"].to_numpy(),
                     "low": df["low"].to_numpy(), "scores": {p: s[f"score_{p}"].to_numpy() for p in cfg["profiles"]}}
    log(f"scores recalculés pour {len(frames)} titres")

    cal = bench.index
    if len(cal) < WARMUP + 70:
        log("historique trop court pour un backtest")
        return 1
    # dernière séance de chaque semaine, après la période de chauffe
    weeks = pd.Series(cal, index=cal).groupby([cal.isocalendar().year, cal.isocalendar().week]).last()
    rebal = [d for d in weeks.sort_values() if d >= cal[WARMUP]]
    q_idx, q_open, q_close = bench.index, bench["open"].to_numpy(), bench["close"].to_numpy()

    def fwd(fr_idx, fr_open, fr_close, fr_low, T, h):
        """Achat à l'ouverture de la séance suivant T, vente à la clôture h séances plus tard."""
        i = fr_idx.searchsorted(T, side="right")
        j = i + h - 1
        if j >= len(fr_idx) or (fr_idx[i] - T).days > 7:
            return None
        entry = fr_open[i]
        if not np.isfinite(entry) or entry <= 0:
            return None
        mae = (np.nanmin(fr_low[i:j + 1]) / entry - 1) if fr_low is not None else 0.0
        return fr_close[j] / entry - 1, mae

    # Référence honnête : moyenne de TOUS les titres de l'univers aux mêmes dates.
    # Si le top 10 ne bat pas cette moyenne, le score n'apporte rien au-delà du choix de l'univers.
    fwd_cache: dict = {}
    universe_avg: dict = {}
    for h in horizons:
        for T in rebal:
            vals = []
            for t, fr in frames.items():
                k = fr["idx"].searchsorted(T, side="right") - 1
                if k < 0 or (T - fr["idx"][k]).days > 5 or not np.isfinite(fr["scores"]["standard"][k] if "standard" in fr["scores"] else np.nan):
                    continue
                r = fwd(fr["idx"], fr["open"], fr["close"], fr["low"], T, h)
                fwd_cache[(t, T, h)] = r
                if r is not None:
                    vals.append(r[0])
            universe_avg[(T, h)] = float(np.mean(vals)) if vals else None

    results, equity = {}, {}
    for p in cfg["profiles"]:
        results[p], equity[p] = {}, {}
        for thr in thresholds:
            per_h = {}
            for h in horizons:
                rets, bres, maes, baskets, ures = [], [], [], [], []
                for T in rebal:
                    cands = []
                    for t, fr in frames.items():
                        k = fr["idx"].searchsorted(T, side="right") - 1
                        if k < 0 or (T - fr["idx"][k]).days > 5:
                            continue
                        v = fr["scores"][p][k]
                        if np.isfinite(v) and v >= thr:
                            cands.append((v, t))
                    cands.sort(reverse=True)
                    qb = fwd(q_idx, q_open, q_close, None, T, h)
                    if qb is None:
                        continue
                    b_rets = []
                    for _, t in cands[:args.top]:
                        fr = frames[t]
                        r = fwd_cache.get((t, T, h)) if (t, T, h) in fwd_cache else fwd(fr["idx"], fr["open"], fr["close"], fr["low"], T, h)
                        if r is None:
                            continue
                        rets.append(r[0]); maes.append(r[1]); bres.append(qb[0]); b_rets.append(r[0])
                        ures.append(universe_avg.get((T, h)) if universe_avg.get((T, h)) is not None else np.nan)
                    baskets.append({"date": f"{T:%Y-%m-%d}", "n": len(b_rets),
                                    "ret": float(np.mean(b_rets)) if b_rets else 0.0, "qqq": qb[0]})
                st = stats(np.array(rets), np.array(bres), np.array(maes))
                if rets:
                    ua = np.array(ures, dtype=float)
                    ok = np.isfinite(ua)
                    st["universe_mean"] = float(ua[ok].mean()) if ok.any() else None
                    st["beat_universe_rate"] = float((np.array(rets)[ok] > ua[ok]).mean()) if ok.any() else None
                    st["excess_vs_universe"] = float((np.array(rets)[ok] - ua[ok]).mean()) if ok.any() else None
                br = np.array([b["ret"] for b in baskets])
                bq = np.array([b["qqq"] for b in baskets])
                st["basket"] = {
                    "rebalances": len(baskets),
                    "empty_rebalances": sum(1 for b in baskets if b["n"] == 0),
                    "mean": float(br.mean()) if len(br) else None,
                    "median": float(np.median(br)) if len(br) else None,
                    "hit_rate": float((br > 0).mean()) if len(br) else None,
                    "beat_qqq_rate": float((br > bq).mean()) if len(br) else None,
                    "worst": float(br.min()) if len(br) else None,
                }
                if h == 5 and len(br):  # 5 séances ≈ 1 semaine : paniers sans chevauchement
                    eq = np.cumprod(1 + br)
                    eqq = np.cumprod(1 + bq)
                    years = len(br) / 52
                    st["basket"].update({
                        "total_return": float(eq[-1] - 1), "cagr": float(eq[-1] ** (1 / years) - 1),
                        "max_drawdown": max_drawdown(np.concatenate([[1.0], eq])),
                        "volatility_ann": float(br.std(ddof=1) * np.sqrt(52)),
                        "qqq_total_return": float(eqq[-1] - 1), "qqq_cagr": float(eqq[-1] ** (1 / years) - 1),
                        "qqq_max_drawdown": max_drawdown(np.concatenate([[1.0], eqq])),
                        "qqq_volatility_ann": float(bq.std(ddof=1) * np.sqrt(52)),
                    })
                    equity[p][str(int(thr))] = [{"date": b["date"], "strat": float(e), "qqq": float(q)}
                                                for b, e, q in zip(baskets, eq, eqq)]
                per_h[str(h)] = st
            results[p][str(int(thr))] = per_h
            s5 = per_h.get("5", {})
            log(f"{p:9s} seuil {thr:>3.0f} : " + " | ".join(
                f"+{h}j moy {per_h[str(h)].get('mean', float('nan')) * 100:+.2f} % (QQQ {per_h[str(h)].get('qqq_mean', float('nan')) * 100:+.2f} %)"
                for h in horizons if per_h[str(h)].get('n')) + (f" | trades {s5.get('n', 0)}" if s5 else ""))

    # QQQ acheté et conservé sur toute la période testée
    start = rebal[0]
    qq = bench["close"].loc[start:]
    daily = qq.pct_change().dropna()
    yrs = len(qq) / 252
    buy_hold = {
        "start": f"{start:%Y-%m-%d}", "end": f"{qq.index[-1]:%Y-%m-%d}",
        "total_return": float(qq.iloc[-1] / qq.iloc[0] - 1),
        "cagr": float((qq.iloc[-1] / qq.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 else None,
        "max_drawdown": max_drawdown(qq.to_numpy()),
        "volatility_ann": float(daily.std() * np.sqrt(252)),
    }

    out = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "period": {"start": f"{rebal[0]:%Y-%m-%d}", "end": f"{rebal[-1]:%Y-%m-%d}", "rebalances": len(rebal)},
        "params": {"top": args.top, "thresholds": thresholds, "horizons": horizons,
                   "entry": "ouverture de la séance suivant le signal", "exit": "clôture après h séances",
                   "rebalance": "dernière séance de chaque semaine", "warmup_sessions": WARMUP,
                   "weights_file": args.weights or "config/weights.json",
                   "profiles": {k: v["weights"] for k, v in cfg["profiles"].items()}},
        "universe": {"count": len(frames), "tickers": sorted(frames)},
        "caveats": [
            "Biais du survivant : l'univers est la liste actuelle de titres, choisie en connaissant leur parcours. Les résultats sont donc optimistes.",
            "Pas de frais de courtage, de spread ni de change EUR/USD.",
            "Les rendements à +20 et +60 séances se chevauchent d'une semaine à l'autre : ils ne s'additionnent pas.",
            "Les titres récents (ex. SNDK, coté depuis février 2025) n'entrent dans le test qu'après leur période de chauffe.",
            "Comparer le top 10 à la moyenne de l'univers (et pas seulement au QQQ) : c'est l'écart avec l'univers qui mesure l'apport réel du score.",
        ],
        "results": results,
        "equity_5d": equity,
        "qqq_buy_hold": buy_hold,
    }
    write_json(data_dir / "backtest" / "results.json", out, compact=True)
    log(f"backtest écrit : {len(rebal)} semaines, du {out['period']['start']} au {out['period']['end']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
