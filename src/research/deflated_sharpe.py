import math

import numpy as np

EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe(n_trials, variance_sr):
    if n_trials <= 1:
        return 0.0
    z = 1.0 - 1.0 / n_trials
    quantile = math.sqrt(2) * _erfinv(2.0 * z - 1.0)
    emax = (1.0 - EULER_GAMMA) * quantile + EULER_GAMMA * math.sqrt(2.0 * math.log(n_trials))
    return math.sqrt(variance_sr) * emax


def _erfinv(x):
    a = 0.147
    ln1 = math.log(1.0 - x)
    ln2 = math.log(1.0 + x)
    p = 2.0 / (math.pi * a) + ln1 / 2.0
    q = ln1 / a
    v = math.sqrt(p * p - q) - p
    return math.copysign(math.sqrt(v), x)


def variance_of_sr(sr, skewness, kurtosis, T):
    denom = max(T - 1, 1)
    return (1.0 - skewness * sr + (kurtosis - 1.0) / 4.0 * sr**2) / denom


def deflated_sharpe(observed_sr, n_trials, T, skewness=0.0, kurtosis=3.0):
    var_sr = variance_of_sr(observed_sr, skewness, kurtosis, T)
    emax = expected_max_sharpe(n_trials, var_sr)
    numerator = (observed_sr - emax) * math.sqrt(max(T - 1, 1))
    denominator = math.sqrt(max(1.0 - skewness * observed_sr
                               + (kurtosis - 1.0) / 4.0 * observed_sr**2, 1e-9))
    return numerator / denominator, emax


def prob_of_deflated_sharpe(observed_sr, n_trials, T, skewness=0.0, kurtosis=3.0):
    z, _ = deflated_sharpe(observed_sr, n_trials, T, skewness, kurtosis)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))