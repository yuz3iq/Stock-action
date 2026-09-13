"""
핵심 신호 로직 (MVP 3종)

  1. TREND SCORE     - 이 종목의 추세는 어떤가? (단기/중기/장기 이동평균 정렬)
  2. CHASE RISK      - 지금 따라 들어가면 늦었는가? (최근 상승폭이 정상 변동 범위를 벗어났는가)
  3. MARKET REGIME   - 지금 시장 환경은 위험자산에 우호적인가? (VIX/지수/달러/유가/금리 종합)

설계 원칙 (기획 리뷰 반영):
  - MARKET REGIME은 "새로운 고유 지표"라고 내세우지 않는다. VIX·추세·매크로를 규칙 기반으로
    조합하는, 흔히 쓰이는 방식이라는 걸 그대로 인정하고 이름도 굳이 지수화하지 않는다
    (예전 초안의 "MPS"라는 이름은 사용하지 않음).
  - 차별점은 지표 자체가 아니라, 세 신호가 "충돌할 때" 무엇을 하라고 말해주는가
    (decide_action)에 있다.
  - 자본금/비중(%) 같은 구체적 배분 수치는 이번 MVP에서 다루지 않는다
    (투자자문업 관련 리스크 때문에 2단계 이후로 미룸).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 1. TREND SCORE
# ---------------------------------------------------------------------------

@dataclass
class TrendResult:
    label: str          # STRONG BULLISH / BULLISH / NEUTRAL / BEARISH / STRONG BEARISH
    score: int           # -3 ~ +3
    detail: dict         # 시간대별(20/50/200) 개별 판정
    explanation: str


def _timeframe_vote(close: pd.Series, window: int, slope_lookback: int) -> int:
    """주어진 이동평균 윈도우에 대해 +1(상승 정렬) / 0(혼조) / -1(하락 정렬) 판정."""
    if len(close) < window + slope_lookback:
        return 0
    ma = close.rolling(window).mean()
    last_price = close.iloc[-1]
    ma_now = ma.iloc[-1]
    ma_prev = ma.iloc[-1 - slope_lookback]

    price_above = last_price > ma_now
    ma_rising = ma_now > ma_prev

    if price_above and ma_rising:
        return 1
    if (not price_above) and (not ma_rising):
        return -1
    return 0


def compute_trend_score(df: pd.DataFrame) -> TrendResult:
    close = df["Close"]

    votes = {
        "short_20d": _timeframe_vote(close, window=20, slope_lookback=5),
        "medium_50d": _timeframe_vote(close, window=50, slope_lookback=10),
        "long_200d": _timeframe_vote(close, window=200, slope_lookback=20),
    }
    score = sum(votes.values())

    if score >= 2:
        label = "STRONG BULLISH"
    elif score == 1:
        label = "BULLISH"
    elif score == 0:
        label = "NEUTRAL"
    elif score == -1:
        label = "BEARISH"
    else:
        label = "STRONG BEARISH"

    names = {"short_20d": "단기(20일)", "medium_50d": "중기(50일)", "long_200d": "장기(200일)"}
    up = [names[k] for k, v in votes.items() if v == 1]
    down = [names[k] for k, v in votes.items() if v == -1]

    if up and down:
        explanation = f"{'·'.join(up)} 추세는 상승, {'·'.join(down)} 추세는 하락으로 시간대별 판단이 엇갈립니다."
    elif up:
        explanation = f"{'·'.join(up)} 추세가 모두 상승 정렬되어 있습니다."
    elif down:
        explanation = f"{'·'.join(down)} 추세가 모두 하락 정렬되어 있습니다."
    else:
        explanation = "뚜렷한 추세 정렬이 없는 혼조 구간입니다."

    return TrendResult(label=label, score=score, detail=votes, explanation=explanation)


# ---------------------------------------------------------------------------
# 2. CHASE RISK
# ---------------------------------------------------------------------------

@dataclass
class ChaseRiskResult:
    label: str          # LOW / MODERATE / HIGH
    return_20d_pct: float
    z_score: float
    atr_extension: float
    explanation: str


def compute_chase_risk(df: pd.DataFrame, lookback_days: int = 20) -> ChaseRiskResult:
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    if len(close) < lookback_days + 60:
        # 데이터가 부족하면 보수적으로 LOW 처리
        ret_20d = float(close.pct_change(lookback_days).iloc[-1] * 100) if len(close) > lookback_days else 0.0
        return ChaseRiskResult("LOW", ret_20d, 0.0, 0.0, "데이터가 충분하지 않아 판정을 보수적으로 처리했습니다.")

    # 최근 N일 수익률
    rolling_return = close.pct_change(lookback_days) * 100
    current_return = rolling_return.iloc[-1]

    # 과거 1년치 N일 수익률 분포 대비 z-score
    hist = rolling_return.dropna()
    mean, std = hist.mean(), hist.std()
    z = (current_return - mean) / std if std > 0 else 0.0

    # ATR(14) 대비 20일선으로부터의 이격도
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean().iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    extension = (close.iloc[-1] - sma20) / atr14 if atr14 > 0 else 0.0

    if z >= 2 or extension >= 2.5:
        label = "HIGH"
    elif z >= 1 or extension >= 1.5:
        label = "MODERATE"
    else:
        label = "LOW"

    explanation = (
        f"최근 {lookback_days}일 상승률 {current_return:+.1f}% (과거 1년 평균 대비 {z:+.1f}표준편차), "
        f"20일선 대비 {extension:+.1f} ATR 이격."
    )

    return ChaseRiskResult(label, float(current_return), float(z), float(extension), explanation)


# ---------------------------------------------------------------------------
# 3. MARKET REGIME
# ---------------------------------------------------------------------------

@dataclass
class RegimeResult:
    label: str          # RISK-ON / NEUTRAL / RISK-OFF
    score: float
    factors: dict
    explanation: str


def _pct_change_n(df: pd.DataFrame | None, n: int) -> float | None:
    if df is None or len(df) < n + 1:
        return None
    close = df["Close"]
    return float((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100)


def compute_market_regime(macro: dict) -> RegimeResult:
    """
    macro: data.fetch_macro_bundle()의 반환값
      {"vix": df, "index": df, "dollar": df, "oil": df, "yield10y": df}

    규칙 기반 종합 판단 (전부 명시적 규칙 — "블랙박스 지표"가 아님):
      - VIX 절대 수준 + 10일 변화
      - 지수(SPY)의 50/200일선 정렬
      - 달러 인덱스 20일 추세
      - WTI 20일 추세 (급등 시 인플레/리스크오프 압력)
      - 10년물 금리 20일 변화 (급등 시 리스크오프 압력)
    """
    factors = {}
    score = 0.0

    vix_df = macro.get("vix")
    if vix_df is not None and len(vix_df) > 10:
        vix_level = float(vix_df["Close"].iloc[-1])
        vix_chg_10d = float(vix_df["Close"].iloc[-1] - vix_df["Close"].iloc[-11])
        if vix_level < 15:
            v = 1.0
        elif vix_level < 22:
            v = 0.0
        else:
            v = -1.0
        v += -0.3 if vix_chg_10d > 2 else (0.3 if vix_chg_10d < -2 else 0.0)
        factors["vix"] = {"level": round(vix_level, 1), "chg_10d": round(vix_chg_10d, 1), "vote": round(v, 2)}
        score += v

    index_df = macro.get("index")
    if index_df is not None and len(index_df) > 200:
        close = index_df["Close"]
        price = close.iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        sma200 = close.rolling(200).mean().iloc[-1]
        if price > sma50 > sma200:
            v = 1.0
        elif price < sma50 < sma200:
            v = -1.0
        else:
            v = 0.0
        factors["index_trend"] = {"above_50d": bool(price > sma50), "above_200d": bool(price > sma200), "vote": v}
        score += v

    dollar_chg = _pct_change_n(macro.get("dollar"), 20)
    if dollar_chg is not None:
        v = 0.5 if dollar_chg < -1.5 else (-0.5 if dollar_chg > 1.5 else 0.0)
        factors["dollar_20d_pct"] = {"value": round(dollar_chg, 1), "vote": v}
        score += v

    oil_chg = _pct_change_n(macro.get("oil"), 20)
    if oil_chg is not None:
        v = -0.5 if oil_chg > 15 else 0.0
        factors["oil_20d_pct"] = {"value": round(oil_chg, 1), "vote": v}
        score += v

    yield_df = macro.get("yield10y")
    if yield_df is not None and len(yield_df) > 20:
        y_chg = float(yield_df["Close"].iloc[-1] - yield_df["Close"].iloc[-21])  # 10Y는 %포인트 단위로 quote됨
        v = -0.5 if y_chg > 0.3 else (0.3 if y_chg < -0.3 else 0.0)
        factors["yield10y_20d_chg"] = {"value": round(y_chg, 2), "vote": v}
        score += v

    if score >= 1.5:
        label = "RISK-ON"
    elif score <= -1.5:
        label = "RISK-OFF"
    else:
        label = "NEUTRAL"

    parts = []
    if "vix" in factors:
        chg = factors["vix"]["chg_10d"]
        parts.append(f"VIX {factors['vix']['level']}(10일 {chg:+.1f})")
    if "index_trend" in factors:
        parts.append("지수 상승 정렬" if factors["index_trend"]["vote"] > 0 else
                      "지수 하락 정렬" if factors["index_trend"]["vote"] < 0 else "지수 혼조")
    if "dollar_20d_pct" in factors:
        parts.append(f"달러 20일 {factors['dollar_20d_pct']['value']:+.1f}%")
    if "oil_20d_pct" in factors:
        parts.append(f"유가 20일 {factors['oil_20d_pct']['value']:+.1f}%")
    if "yield10y_20d_chg" in factors:
        parts.append(f"10년물 금리 20일 {factors['yield10y_20d_chg']['value']:+.2f}%p")
    explanation = f"{', '.join(parts)} 등을 종합하면 현재 시장 환경은 {label}에 가깝습니다. (종합 점수 {score:+.1f})"

    return RegimeResult(label, round(score, 2), factors, explanation)


# ---------------------------------------------------------------------------
# 4. ACTION (충돌 판정) — 자본 배분은 다루지 않음, 방향성 판단까지만
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    verdict: str   # ACCUMULATE / WAIT / WAIT FOR PULLBACK / WATCH / AVOID
    reason: str


def decide_action(trend: TrendResult, chase: ChaseRiskResult, regime: RegimeResult) -> ActionResult:
    trend_positive = trend.label in ("BULLISH", "STRONG BULLISH")
    trend_negative = trend.label in ("BEARISH", "STRONG BEARISH")
    chase_high = chase.label == "HIGH"
    regime_positive = regime.label == "RISK-ON"
    regime_negative = regime.label == "RISK-OFF"

    if trend_negative and regime_negative:
        return ActionResult(
            "AVOID",
            "종목 추세와 시장 환경이 모두 부정적으로 정렬되어 있습니다. 신규 진입 근거가 없습니다.",
        )

    if trend_positive and chase_high:
        return ActionResult(
            "WAIT FOR PULLBACK",
            f"종목 추세는 {trend.label}이지만, 최근 상승 속도가 정상 범위를 벗어났습니다({chase.explanation}) "
            "지금 따라 들어가기보다 조정을 기다리는 편이 유리합니다.",
        )

    if trend_positive and regime_negative:
        return ActionResult(
            "WAIT",
            f"종목 자체의 추세는 {trend.label}이지만, 시장 환경이 RISK-OFF({regime.explanation}) 상태입니다. "
            "추세와 시장환경이 충돌하고 있어 신규 진입의 기대 위험 대비 효율이 낮습니다.",
        )

    if trend_positive and regime_positive and not chase_high:
        return ActionResult(
            "ACCUMULATE",
            f"종목 추세({trend.label})와 시장 환경(RISK-ON) 방향이 일치하고, 추격매수 위험도 낮습니다.",
        )

    if trend_negative and regime_positive:
        return ActionResult(
            "WATCH",
            f"시장 환경은 우호적이지만 이 종목 자체의 추세는 {trend.label}입니다. "
            "시장이 아니라 종목 고유의 약세이므로 반등 신호를 확인하기 전까지는 관망합니다.",
        )

    return ActionResult(
        "WATCH",
        "추세·추격위험·시장환경 중 뚜렷하게 한쪽으로 정렬된 신호가 없습니다. 추가 확인이 필요합니다.",
    )


# ---------------------------------------------------------------------------
# 5. PORTFOLIO PLAN — 자본 배분 (개인 전용 사용을 전제로 한 확신도 기반 등급제)
# ---------------------------------------------------------------------------
#
# 개인이 혼자 쓰는 도구라는 전제 하에 구체적인 금액/비중까지 계산한다.
# 규칙은 의도적으로 단순하게 유지한다:
#   - 종목별 목표 비중은 ACTION 판정 + TREND 강도로만 정해지는 확신도 등급제.
#   - 위험성향(보수/중립/공격) x 시장환경(RISK-ON/NEUTRAL/RISK-OFF) 조합으로
#     "전체 주식비중 상한"을 먼저 정하고, 종목별 목표 비중 합이 상한을 넘으면
#     비례해서 축소한다. 상한 밑으로 남는 부분은 전부 현금으로 둔다
#     ("확신 있는 만큼만 투자").

RISK_PROFILES = {
    "Conservative": {"RISK-ON": 55, "NEUTRAL": 45, "RISK-OFF": 25},
    "Moderate": {"RISK-ON": 80, "NEUTRAL": 65, "RISK-OFF": 45},
    "Aggressive": {"RISK-ON": 95, "NEUTRAL": 80, "RISK-OFF": 65},
}


def weight_for_action(trend: TrendResult, verdict: str) -> float:
    """ACCUMULATE가 아니면 신규 비중 0. ACCUMULATE면 추세 강도에 따라 15% / 8%."""
    if verdict != "ACCUMULATE":
        return 0.0
    return 15.0 if trend.label == "STRONG BULLISH" else 8.0


@dataclass
class HoldingRow:
    ticker: str
    price: float
    trend: TrendResult
    chase: ChaseRiskResult
    action: ActionResult
    raw_weight: float
    weight: float = 0.0

    @property
    def amount(self) -> float:
        return 0.0  # app.py에서 capital과 곱해 채움


@dataclass
class PortfolioPlan:
    rows: list
    equity_pct: float
    cash_pct: float
    ceiling_pct: float
    risk_profile: str
    regime: RegimeResult


def build_portfolio_plan(holdings_raw: list[tuple[str, float, TrendResult, ChaseRiskResult, ActionResult]],
                          regime: RegimeResult, risk_profile: str) -> PortfolioPlan:
    """
    holdings_raw: [(ticker, price, trend, chase, action), ...]
    """
    ceiling = RISK_PROFILES[risk_profile][regime.label]

    rows = []
    for ticker, price, trend, chase, action in holdings_raw:
        raw_w = weight_for_action(trend, action.verdict)
        rows.append(HoldingRow(ticker=ticker, price=price, trend=trend, chase=chase, action=action, raw_weight=raw_w))

    sum_raw = sum(r.raw_weight for r in rows)
    scale = (ceiling / sum_raw) if (sum_raw > ceiling and sum_raw > 0) else 1.0
    for r in rows:
        r.weight = round(r.raw_weight * scale, 1)

    equity_pct = round(sum(r.weight for r in rows), 1)
    cash_pct = round(100 - equity_pct, 1)

    return PortfolioPlan(rows=rows, equity_pct=equity_pct, cash_pct=cash_pct, ceiling_pct=ceiling,
                          risk_profile=risk_profile, regime=regime)
