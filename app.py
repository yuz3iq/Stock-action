"""
STOCK ACTION DASHBOARD (MVP)

티커를 입력하면
  1) TREND SCORE   - 이 종목의 추세
  2) CHASE RISK    - 지금 따라 들어가면 늦었는가
  3) MARKET REGIME - 지금 시장 환경
세 신호를 계산하고, 세 신호가 충돌하는지 여부에 따라 ACCUMULATE / WAIT / WAIT FOR
PULLBACK / WATCH / AVOID 중 하나의 판단(ACTION)을 보여주는 웹 대시보드.

범위 밖(2단계 이후로 미룸): 자본금 입력·비중(%) 배분, RECOVERY SCORE,
IF I'M WRONG, 포지션 관리(부분 익절/트레일링).

실행 방법:
    pip install -r requirements.txt
    python app.py
    -> http://127.0.0.1:5000 접속
"""

from __future__ import annotations

from flask import Flask, render_template, request

import data
import signals

app = Flask(__name__)

_BADGE_CLASS = {
    "STRONG BULLISH": "pos", "BULLISH": "pos",
    "NEUTRAL": "neutral",
    "BEARISH": "neg", "STRONG BEARISH": "neg",
    "LOW": "pos", "MODERATE": "warn", "HIGH": "neg",
    "RISK-ON": "pos", "RISK-OFF": "neg",
    "ACCUMULATE": "pos", "WATCH": "neutral",
    "WAIT": "warn", "WAIT FOR PULLBACK": "warn",
    "AVOID": "neg",
}


@app.template_filter("badge_class")
def badge_class(label: str) -> str:
    return _BADGE_CLASS.get(label, "neutral")

# 매크로 데이터는 종목마다 새로 받을 필요가 없으므로 프로세스 내에서 간단히 캐싱한다.
_macro_cache = {"bundle": None}


def get_macro_bundle():
    if _macro_cache["bundle"] is None:
        _macro_cache["bundle"] = data.fetch_macro_bundle()
    return _macro_cache["bundle"]


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["GET"])
def analyze():
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return render_template("index.html", error="티커를 입력해주세요.")

    try:
        price_df = data.fetch_price_history(ticker)
    except Exception as e:
        return render_template("index.html", error=str(e), ticker=ticker)

    try:
        macro = get_macro_bundle()
    except Exception as e:
        return render_template("index.html", error=f"매크로 데이터 조회 실패: {e}", ticker=ticker)

    trend = signals.compute_trend_score(price_df)
    chase = signals.compute_chase_risk(price_df)
    regime = signals.compute_market_regime(macro)
    action = signals.decide_action(trend, chase, regime)

    last_price = float(price_df["Close"].iloc[-1])

    return render_template(
        "result.html",
        ticker=ticker,
        last_price=last_price,
        trend=trend,
        chase=chase,
        regime=regime,
        action=action,
    )


DEFAULT_WATCHLIST = "AAPL,MSFT,NVDA,GOOGL,AMZN,TSLA,META,JPM,XOM,JNJ"


def _parse_capital(raw: str) -> int:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return int(digits) if digits else 0


@app.route("/portfolio", methods=["GET"])
def portfolio():
    tickers_raw = request.args.get("tickers", DEFAULT_WATCHLIST)
    tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
    capital = _parse_capital(request.args.get("capital", "30000000"))
    risk_profile = request.args.get("risk", "Moderate")
    if risk_profile not in signals.RISK_PROFILES:
        risk_profile = "Moderate"

    try:
        macro = get_macro_bundle()
    except Exception as e:
        return render_template(
            "portfolio.html",
            error=f"매크로 데이터 조회 실패: {e}",
            tickers_raw=tickers_raw, capital=capital, risk_profile=risk_profile,
        )
    regime = signals.compute_market_regime(macro)

    holdings_raw = []
    errors = []
    for tk in tickers:
        try:
            df = data.fetch_price_history(tk)
        except Exception as e:
            errors.append(f"{tk}: {e}")
            continue
        trend = signals.compute_trend_score(df)
        chase = signals.compute_chase_risk(df)
        action = signals.decide_action(trend, chase, regime)
        price = float(df["Close"].iloc[-1])
        holdings_raw.append((tk, price, trend, chase, action))

    plan = signals.build_portfolio_plan(holdings_raw, regime, risk_profile)

    return render_template(
        "portfolio.html",
        plan=plan,
        capital=capital,
        tickers_raw=tickers_raw,
        risk_profile=risk_profile,
        errors=errors,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
