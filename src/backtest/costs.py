import numpy as np


def per_trade_cost(config):
    return config["costs"]["taker_fee"] * 2 + config["costs"]["slippage"]


def strategy_returns(returns, position, config):
    held = np.roll(position, 1)
    held[0] = 0.0
    gross = held * returns
    turnover = np.abs(np.diff(np.concatenate([[0.0], position])))
    net = gross - turnover * per_trade_cost(config)
    return net