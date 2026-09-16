import numpy as np


def compute_metrics(returns, position, periods_per_year=365):
    returns = np.asarray(returns, dtype=float)
    position = np.asarray(position, dtype=float)

    n = len(returns)
    exposure = float(np.mean(position != 0))
    avg_gross = float(np.mean(np.abs(position)))

    prev = np.concatenate([[0.0], position[:-1]])
    entries = np.sum((np.abs(prev) < 1e-12) & (np.abs(position) > 1e-12))
    n_trades = int(entries)
    turnover = float(np.sum(np.abs(np.diff(np.concatenate([[0.0], position])))))

    eq = np.cumprod(1.0 + returns)
    total_return = float(eq[-1] - 1.0)
    years = n / periods_per_year
    cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0) if years > 0 and total_return > -1 else -1.0
    ann_vol = float(np.std(returns, ddof=1) * np.sqrt(periods_per_year)) if n > 1 else 0.0
    std = np.std(returns, ddof=1) if n > 1 else 0.0
    sharpe = float(np.mean(returns) / std * np.sqrt(periods_per_year)) if std > 0 else 0.0

    downside = returns[returns < 0]
    dstd = np.std(downside, ddof=1) if downside.size > 1 else 0.0
    sortino = (
        float(np.mean(returns) / dstd * np.sqrt(periods_per_year)) if dstd > 0 else 0.0
    )

    drawdown = eq / np.maximum.accumulate(eq) - 1.0
    max_dd = float(drawdown.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    gains = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    profit_factor = float(gains / losses) if losses > 0 else float("inf") if gains > 0 else 0.0
    active = returns[np.abs(position) > 1e-12]
    win_rate = float(np.mean(active > 0)) if active.size else 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "exposure": exposure,
        "avg_gross": avg_gross,
        "turnover": turnover,
        "n_trades": n_trades,
        "n_days": int(n),
    }
