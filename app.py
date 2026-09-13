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

import datetime as dt
import os

from flask import Flask, jsonify, redirect, render_template, request, url_for

import data
import signals
import store

app = Flask(__name__)

# 크론잡이 /snapshot을 두드릴 때 아무나 못 부르게 하는 간단한 토큰(선택사항).
# 환경변수로 안 심어두면 그냥 인증 없이 열려있음(개인용 MVP라 크게 위험하진 않음).
SNAPSHOT_TOKEN = os.environ.get("SNAPSHOT_TOKEN", "")

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
    today = dt.date.today().isoformat()
    today_changes = store.get_changes_today(today)
    return render_template("index.html", today_changes=today_changes)


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


def _parse_capital(raw: str) -> int:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return int(digits) if digits else 0


@app.route("/portfolio", methods=["GET"])
def portfolio():
    saved = store.get_portfolio_settings()
    tickers_raw = request.args.get("tickers", saved["tickers"])
    tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
    capital = _parse_capital(request.args.get("capital", str(saved["capital"])))
    risk_profile = request.args.get("risk", saved["risk"])
    if risk_profile not in signals.RISK_PROFILES:
        risk_profile = "Moderate"

    # 다음 방문 때도 그대로 뜨도록 저장 ("매번 다시 입력 안 해도 되게")
    store.set_portfolio_settings(tickers_raw, capital, risk_profile)

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


def evaluate_trade(t: dict) -> dict:
    """
    실제 매매 기록 하나를 받아서, 그 매매일 시점으로 돌아가 시스템이 뭐라고 했을지
    소급 계산하고("미래를 보지 않고" — data.py의 as_of 파라미터로 그 날짜까지의 데이터만 사용),
    실제 행동(BUY/SELL)과 비교한다.
    """
    ticker = t["ticker"]
    trade_date = dt.datetime.strptime(t["date"], "%Y-%m-%d").date()

    hist_df = data.fetch_price_history(ticker, as_of=trade_date)
    hist_macro = data.fetch_macro_bundle(as_of=trade_date)

    trend = signals.compute_trend_score(hist_df)
    chase = signals.compute_chase_risk(hist_df)
    regime = signals.compute_market_regime(hist_macro)
    action = signals.decide_action(trend, chase, regime)

    current_df = data.fetch_price_history(ticker)
    current_price = float(current_df["Close"].iloc[-1])

    entry_price = float(t["price"])
    side = t.get("side", "BUY").upper()

    if side == "SELL":
        pct_since = (entry_price - current_price) / entry_price * 100
        aligned = action.verdict in ("AVOID", "WAIT", "WAIT FOR PULLBACK")
    else:
        pct_since = (current_price - entry_price) / entry_price * 100
        aligned = action.verdict == "ACCUMULATE"

    return {
        "trade": t,
        "trend": trend,
        "chase": chase,
        "regime": regime,
        "action": action,
        "current_price": current_price,
        "pct_since": pct_since,
        "aligned": aligned,
    }


@app.route("/trades", methods=["GET"])
def trades():
    records = store.get_trades()
    evaluated = []
    errors = []
    for t in records:
        try:
            evaluated.append(evaluate_trade(t))
        except Exception as e:
            errors.append(f"{t['ticker']} ({t['date']}): {e}")
    # 최근 매매가 위로 오게
    evaluated.sort(key=lambda e: e["trade"]["date"], reverse=True)
    return render_template("trades.html", trades=evaluated, errors=errors)


@app.route("/trades/add", methods=["POST"])
def trades_add():
    ticker = request.form.get("ticker", "").strip().upper()
    date_str = request.form.get("date", "").strip()
    side = request.form.get("side", "BUY").strip().upper()
    try:
        price = float(request.form.get("price", "0"))
    except ValueError:
        price = 0.0
    try:
        quantity = float(request.form.get("quantity", "0") or 0)
    except ValueError:
        quantity = 0.0

    if ticker and date_str and price > 0:
        store.add_trade(ticker, date_str, price, quantity, side)
    return redirect(url_for("trades"))


@app.route("/trades/delete/<trade_id>", methods=["POST"])
def trades_delete(trade_id):
    store.delete_trade(trade_id)
    return redirect(url_for("trades"))


def snapshot_watchlist() -> dict:
    """
    관심종목(Portfolio Plan에 등록된 티커) 전체에 대해 오늘자 판단을 계산해서 기록한다.
    이미 오늘 기록이 있는 종목은 다시 계산하지 않는다(하루 1번). 새로 기록하면서
    바로 전날 기록과 판단(verdict)이 달라진 경우 "changed"에 담아 반환한다.

    이 함수는 두 군데서 호출된다:
      1) /snapshot — Render Cron Job이 매일 한 번 이 엔드포인트를 호출
      2) /changes — 사용자가 화면을 열었는데 그날 스냅샷이 아직 없으면 그 자리에서 계산
         (크론잡이 아직 안 돌았거나 실패했어도 방문하면 최신 상태가 되도록)
    """
    today = dt.date.today().isoformat()
    tickers = store.get_watchlist_tickers()

    try:
        macro = get_macro_bundle()
    except Exception as e:
        return {"date": today, "recorded": 0, "changed": [], "errors": [f"매크로 데이터 조회 실패: {e}"]}

    changed = []
    errors = []
    recorded = 0
    for tk in tickers:
        try:
            prev_history = store.get_snapshots(tk)
            prev_verdict = prev_history[-1]["verdict"] if prev_history else None

            df = data.fetch_price_history(tk)
            trend = signals.compute_trend_score(df)
            chase = signals.compute_chase_risk(df)
            regime = signals.compute_market_regime(macro)
            action = signals.decide_action(trend, chase, regime)

            was_new = store.record_snapshot(tk, today, action.verdict, trend.label, chase.label, regime.label)
            if was_new:
                recorded += 1
                if prev_verdict is not None and prev_verdict != action.verdict:
                    changed.append({"ticker": tk, "from": prev_verdict, "to": action.verdict})
        except Exception as e:
            errors.append(f"{tk}: {e}")

    return {"date": today, "recorded": recorded, "changed": changed, "errors": errors}


@app.route("/snapshot", methods=["GET", "POST"])
def snapshot():
    """Render Cron Job이 매일 한 번 호출하는 용도. 브라우저로 직접 열어도 동작함."""
    if SNAPSHOT_TOKEN and request.args.get("token") != SNAPSHOT_TOKEN:
        return jsonify({"error": "unauthorized"}), 403
    result = snapshot_watchlist()
    return jsonify(result)


@app.route("/changes", methods=["GET"])
def changes():
    result = snapshot_watchlist()  # 오늘 기록이 이미 있으면 아무 것도 안 하고 바로 리턴됨
    all_snapshots = store.get_all_snapshots()
    tickers = store.get_watchlist_tickers()

    rows = []
    for tk in tickers:
        history = all_snapshots.get(tk, [])
        if not history:
            continue
        latest = history[-1]
        prev = history[-2] if len(history) >= 2 else None
        changed_today = bool(prev and prev["verdict"] != latest["verdict"])
        rows.append({
            "ticker": tk,
            "latest": latest,
            "prev": prev,
            "changed": changed_today,
            "history": list(reversed(history[-10:])),
        })
    # 오늘 판단이 바뀐 종목을 위로 정렬
    rows.sort(key=lambda r: (not r["changed"], r["ticker"]))

    return render_template("changes.html", rows=rows, errors=result["errors"], today=result["date"])


if __name__ == "__main__":
    app.run(debug=True, port=5000)
