"""
네트워크 없이(yfinance 없이) signals.py 계산 로직만 검증하는 스크립트.
합성 데이터(synthetic OHLCV)로 각 시나리오에서 기대한 라벨이 나오는지 확인한다.

실행: python3 test_signals.py
"""

import numpy as np
import pandas as pd

from signals import compute_trend_score, compute_chase_risk, compute_market_regime, decide_action


def make_ohlcv(closes: np.ndarray, end_date=None) -> pd.DataFrame:
    """
    합성 OHLCV를 만든다. end_date를 주지 않으면 오늘 날짜로 끝나는 영업일 인덱스를 붙인다
    (실제 yfinance 데이터처럼 DatetimeIndex를 갖게 해서, 날짜 비교가 필요한 로직
    — 예: 포지션의 "진입일 이후 고점" 계산, 백테스트의 날짜별 슬라이스 — 도 검증할 수 있게 한다).
    """
    closes = np.asarray(closes, dtype=float)
    high = closes * 1.01
    low = closes * 0.99
    n = len(closes)
    if end_date is None:
        end_date = pd.Timestamp.today().normalize()
    # pd.bdate_range(end=..., periods=n)는 end가 영업일이 아닐 때 n보다 하나 적게 주는
    # 경우가 있어(pandas 이슈), 여유분을 더 뽑은 뒤 뒤에서 n개만 잘라 쓴다.
    dates = pd.bdate_range(end=end_date, periods=n + 5)[-n:]
    df = pd.DataFrame({"Close": closes, "High": high, "Low": low}, index=dates)
    return df


def uptrend_series(n=300, daily_drift=0.0015, noise=0.006, seed=1):
    rng = np.random.default_rng(seed)
    rets = daily_drift + rng.normal(0, noise, n)
    closes = 100 * np.cumprod(1 + rets)
    return closes


def downtrend_series(n=300, daily_drift=-0.0015, noise=0.006, seed=2):
    rng = np.random.default_rng(seed)
    rets = daily_drift + rng.normal(0, noise, n)
    closes = 100 * np.cumprod(1 + rets)
    return closes


def flat_series(n=300, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, noise, n)
    closes = 100 * np.cumprod(1 + rets)
    return closes


def spike_series(n=300, seed=4):
    """장기 상승 추세 + 최근 20일 급등 (CHASE RISK HIGH 시나리오)"""
    base = uptrend_series(n - 20, daily_drift=0.0008, noise=0.006, seed=seed)
    last = base[-1]
    spike_days = 20
    spike_rets = np.full(spike_days, 0.02)  # 하루 2%씩 20일 = 급등
    spike = last * np.cumprod(1 + spike_rets)
    return np.concatenate([base, spike])


def run_case(name, closes):
    df = make_ohlcv(closes)
    trend = compute_trend_score(df)
    chase = compute_chase_risk(df)
    print(f"\n=== {name} ===")
    print(f"TREND  : {trend.label} (score={trend.score})  - {trend.explanation}")
    print(f"CHASE  : {chase.label}  - {chase.explanation}")
    return trend, chase


def smooth_trend(n, daily_drift, noise=0.001, seed=0):
    """레짐 테스트용: 잡음이 작아 SMA 정렬이 뚜렷하게 나오는 부드러운 추세 곡선."""
    rng = np.random.default_rng(seed)
    rets = daily_drift + rng.normal(0, noise, n)
    return 100 * np.cumprod(1 + rets)


def run_regime_case(name, vix_level, index_uptrend, dollar_flat=True):
    n = 260
    idx_closes = smooth_trend(n, daily_drift=0.003, seed=10) if index_uptrend else smooth_trend(n, daily_drift=-0.003, seed=11)
    vix_closes = np.full(n, vix_level) + np.random.default_rng(5).normal(0, 0.3, n)
    dollar_closes = flat_series(n, noise=0.001, seed=6)
    oil_closes = flat_series(n, noise=0.003, seed=7)
    yield_closes = np.full(n, 4.0) + np.random.default_rng(8).normal(0, 0.01, n)

    macro = {
        "vix": make_ohlcv(vix_closes),
        "index": make_ohlcv(idx_closes),
        "dollar": make_ohlcv(dollar_closes),
        "oil": make_ohlcv(oil_closes),
        "yield10y": make_ohlcv(yield_closes),
    }
    regime = compute_market_regime(macro)
    print(f"\n=== {name} ===")
    print(f"REGIME : {regime.label} (score={regime.score})  - {regime.explanation}")
    return regime


if __name__ == "__main__":
    print("### TREND / CHASE RISK 시나리오 ###")
    t_up, c_up = run_case("우상향 추세", uptrend_series())
    assert t_up.label in ("BULLISH", "STRONG BULLISH"), t_up.label

    t_down, c_down = run_case("하락 추세", downtrend_series())
    assert t_down.label in ("BEARISH", "STRONG BEARISH"), t_down.label

    t_flat, c_flat = run_case("횡보", flat_series())

    t_spike, c_spike = run_case("장기 상승 + 최근 20일 급등 (추격매수 위험)", spike_series())
    assert c_spike.label == "HIGH", c_spike.label

    print("\n### MARKET REGIME 시나리오 ###")
    r_on = run_regime_case("Risk-On (VIX 낮음 + 지수 상승)", vix_level=12, index_uptrend=True)
    assert r_on.label == "RISK-ON", r_on.label

    r_off = run_regime_case("Risk-Off (VIX 높음 + 지수 하락)", vix_level=32, index_uptrend=False)
    assert r_off.label == "RISK-OFF", r_off.label

    print("\n### ACTION 충돌 판정 시나리오 ###")
    a1 = decide_action(t_up, c_up, r_on)
    print(f"상승추세 + 낮은추격위험 + RISK-ON  -> {a1.verdict} | {a1.reason}")
    assert a1.verdict == "ACCUMULATE"

    a2 = decide_action(t_up, c_up, r_off)
    print(f"상승추세 + 낮은추격위험 + RISK-OFF -> {a2.verdict} | {a2.reason}")
    assert a2.verdict == "WAIT"

    a3 = decide_action(t_spike, c_spike, r_on)
    print(f"상승추세 + 추격위험HIGH + RISK-ON  -> {a3.verdict} | {a3.reason}")
    assert a3.verdict == "WAIT FOR PULLBACK"

    a4 = decide_action(t_down, c_down, r_off)
    print(f"하락추세 + RISK-OFF               -> {a4.verdict} | {a4.reason}")
    assert a4.verdict == "AVOID"

    print("\n모든 시나리오 통과.")
