"""Small, dependency-free forecasting: damped-trend exponential smoothing (Holt) with honest uncertainty.

Every forecast carries a rolling-origin back-test against the naive "tomorrow = today" baseline. Callers must treat a
forecast whose ``skill`` is <= 0 as no better than a guess.
"""
from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Sequence

Z80, Z95 = 1.2816, 1.96


def _fit(y: Sequence[float], alpha: float, beta: float, phi: float) -> tuple[float, float, list[float]]:
    """One pass of damped Holt; returns final level, trend and one-step-ahead fitted values."""
    level, trend = y[0], (y[1] - y[0]) if len(y) > 1 else 0.0
    fitted = [y[0]]
    for t in range(1, len(y)):
        fitted.append(level + phi * trend)
        prev = level
        level = alpha * y[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev) + (1 - beta) * phi * trend
    return level, trend, fitted


def _best_params(y: Sequence[float]) -> tuple[float, float, float]:
    best, params = math.inf, (0.5, 0.1, 0.9)
    for a in (0.2, 0.4, 0.6, 0.8, 0.95):
        for b in (0.05, 0.15, 0.3):
            for phi in (0.8, 0.9, 0.98):
                _, _, f = _fit(y, a, b, phi)
                sse = sum((yt - ft) ** 2 for yt, ft in zip(y[1:], f[1:]))
                if sse < best:
                    best, params = sse, (a, b, phi)
    return params


def holt_forecast(values: Sequence[float], horizon: int = 6, backtest: int = 12) -> dict:
    """Forecast ``horizon`` steps. Needs >= 8 points; returns ``{}`` otherwise (callers must not invent a forecast)."""
    y = [float(v) for v in values if v is not None]
    if len(y) < 8:
        return {}
    a, b, phi = _best_params(y)
    level, trend, fitted = _fit(y, a, b, phi)
    resid = [yt - ft for yt, ft in zip(y[1:], fitted[1:])]
    sigma = pstdev(resid) if len(resid) > 1 else 0.0
    points, damp = [], 0.0
    for h in range(1, horizon + 1):
        damp += phi ** h
        p50 = level + damp * trend
        # variance grows with the horizon (random-walk-like), a deliberately wide, conservative interval
        sd = sigma * math.sqrt(h)
        points.append({"h": h, "p50": p50, "lo80": p50 - Z80 * sd, "hi80": p50 + Z80 * sd,
                       "lo95": p50 - Z95 * sd, "hi95": p50 + Z95 * sd})
    out = {"method": "damped Holt (alpha=%.2f beta=%.2f phi=%.2f)" % (a, b, phi), "horizon": horizon,
           "points": points, "residual_sd": sigma, "n": len(y)}
    out["backtest"] = _backtest(y, backtest)
    return out


def _backtest(y: Sequence[float], k: int) -> dict:
    """Rolling-origin, one-step-ahead: model vs naive. skill = 1 - MAE_model / MAE_naive (> 0 means better than naive)."""
    n = len(y)
    k = min(k, n - 6)
    if k < 3:
        return {"n": 0, "skill": None}
    mae_m = mae_n = 0.0
    for t in range(n - k, n):
        train = y[:t]
        a, b, phi = _best_params(train)
        level, trend, _ = _fit(train, a, b, phi)
        mae_m += abs(y[t] - (level + phi * trend))
        mae_n += abs(y[t] - train[-1])
    mae_m, mae_n = mae_m / k, mae_n / k
    return {"n": k, "mae_model": mae_m, "mae_naive": mae_n, "skill": (1 - mae_m / mae_n) if mae_n else None}


def volatility_cone(values: Sequence[float], horizon: int = 30) -> dict:
    """Random-walk band for series where a trend forecast would be dishonest (FX). No directional claim is made."""
    y = [float(v) for v in values if v and v > 0]
    if len(y) < 20:
        return {}
    rets = [math.log(y[i] / y[i - 1]) for i in range(1, len(y))]
    sd = pstdev(rets)
    last = y[-1]
    pts = []
    for h in (5, 10, 20, horizon):
        s = sd * math.sqrt(h)
        pts.append({"h": h, "p50": last, "lo80": last * math.exp(-Z80 * s), "hi80": last * math.exp(Z80 * s),
                    "lo95": last * math.exp(-Z95 * s), "hi95": last * math.exp(Z95 * s)})
    return {"method": "random walk, volatility cone (no directional forecast)", "horizon": horizon, "points": pts,
            "daily_vol": sd, "n": len(y), "backtest": {"n": 0, "skill": None}}


def stats(values: Sequence[float]) -> dict:
    """Level/dispersion summary used by several agents."""
    y = [float(v) for v in values if v is not None]
    if not y:
        return {}
    rets = [math.log(y[i] / y[i - 1]) for i in range(1, len(y)) if y[i] > 0 and y[i - 1] > 0]
    return {"n": len(y), "last": y[-1], "mean": fmean(y), "sd": pstdev(y) if len(y) > 1 else 0.0,
            "peak": max(y), "trough": min(y), "monthly_vol": pstdev(rets) if len(rets) > 1 else None,
            "drawdown_from_peak": (y[-1] - max(y)) / max(y) if max(y) else None}


def linear_trend_forecast(xs: Sequence[float], ys: Sequence[float], at: Sequence[float]) -> dict:
    """Ordinary least squares with prediction intervals, for short annual series where Holt would be overfitting.

    Needs >= 5 points. No back-test is possible on so little data, so the result is flagged ``indicative``.
    """
    n = len(xs)
    if n < 5 or n != len(ys):
        return {}
    xb, yb = fmean(xs), fmean(ys)
    sxx = sum((x - xb) ** 2 for x in xs)
    if sxx == 0:
        return {}
    slope = sum((x - xb) * (y - yb) for x, y in zip(xs, ys)) / sxx
    icpt = yb - slope * xb
    resid = [y - (icpt + slope * x) for x, y in zip(xs, ys)]
    s = math.sqrt(sum(r * r for r in resid) / (n - 2)) if n > 2 else 0.0
    pts = []
    for i, x0 in enumerate(at, 1):
        p = icpt + slope * x0
        se = s * math.sqrt(1 + 1 / n + (x0 - xb) ** 2 / sxx)
        pts.append({"h": i, "x": x0, "p50": p, "lo80": p - Z80 * se, "hi80": p + Z80 * se, "lo95": p - Z95 * se, "hi95": p + Z95 * se})
    return {"method": "linear trend (OLS)", "slope_per_year": slope, "residual_sd": s, "n": n, "points": pts,
            "indicative": True, "backtest": {"n": 0, "skill": None}}
