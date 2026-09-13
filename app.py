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
    # RECOVERY SCORE
    "N/A": "neutral", "NONE": "neutral", "EARLY": "warn", "CONFIRMED": "pos",
    # IF I'M WRONG (ThesisCheck.status) — "OK"는 위에서 이미 pos로 안 쓰이니 새로 추가
    "OK": "pos", "CAUTION": "warn", "INVALIDATED": "neg",
    # POSITION MANAGEMENT (PositionAdvice.label)
    "HOLD": "neutral", "CONSIDER_PARTIAL_PROFIT": "warn", "TRAILING_STOP_HIT": "neg",
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
    recovery = signals.compute_recovery_score(price_df, trend)

    last_price = float(price_df["Close"].iloc[-1])

    return render_template(
        "result.html",
        ticker=ticker,
        last_price=last_price,
        trend=trend,
        chase=chase,
        regime=regime,
        action=action,
        recovery=recovery,
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


@app.route("/positions", methods=["GET"])
def positions():
    """
    "포지션 입력"은 별도 화면 없이 /trades의 매매 기록에서 자동으로 계산한다(store.get_open_positions).
    각 보유 종목에 대해 오늘 기준 신호를 새로 계산하고, IF I'M WRONG(가설 무효화)과
    POSITION MANAGEMENT(부분 익절/트레일링) 판단까지 한 화면에서 보여준다.
    """
    open_positions = store.get_open_positions()

    try:
        macro = get_macro_bundle()
        index_df = macro.get("index")
    except Exception as e:
        return render_template("positions.html", rows=[], errors=[f"매크로 데이터 조회 실패: {e}"])

    regime = signals.compute_market_regime(macro)

    rows = []
    errors = []
    for pos in open_positions:
        try:
            df = data.fetch_price_history(pos["ticker"])
            trend = signals.compute_trend_score(df)
            chase = signals.compute_chase_risk(df)
            rel_strength = signals.compute_relative_strength(df, index_df)
            thesis = signals.check_thesis("BUY", trend, regime, rel_strength)
            advice = signals.compute_position_advice(df, pos["entry_date"], pos["avg_price"], chase)
            current_price = float(df["Close"].iloc[-1])
            rows.append({
                "position": pos, "trend": trend, "chase": chase, "regime": regime,
                "thesis": thesis, "advice": advice, "current_price": current_price,
            })
        except Exception as e:
            errors.append(f"{pos['ticker']}: {e}")

    return render_template("positions.html", rows=rows, errors=errors)


@app.route("/compare-risk", methods=["GET"])
def compare_risk():
    """관심종목 그대로, 위험성향 3개(Conservative/Moderate/Aggressive)를 동시에 계산해서 나란히 비교."""
    saved = store.get_portfolio_settings()
    tickers_raw = request.args.get("tickers", saved["tickers"])
    tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
    capital = _parse_capital(request.args.get("capital", str(saved["capital"])))

    try:
        macro = get_macro_bundle()
    except Exception as e:
        return render_template("compare_risk.html", error=f"매크로 데이터 조회 실패: {e}", tickers_raw=tickers_raw, capital=capital)
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

    plans = {rp: signals.build_portfolio_plan(holdings_raw, regime, rp) for rp in signals.RISK_PROFILES}

    return render_template("compare_risk.html", plans=plans, capital=capital, tickers_raw=tickers_raw, errors=errors)


@app.route("/backtest", methods=["GET"])
def backtest():
    """
    지정한 종목 + 기간에 대해, 그 구간 동안 매주(5거래일)마다 시스템이 뭐라고 했을지 소급 계산해서
    타임라인으로 보여주고 "시스템 판단을 따랐다면"과 "그냥 사서 들고 있었다면"의 단순 수익률을 비교한다.
    구간 내 모든 날짜를 매번 새로 fetch하면 느리므로, data.fetch_price_history_span으로 구간 전체를
    한 번만 받아온 뒤 날짜별로 슬라이스해서 재사용한다.
    """
    ticker = request.args.get("ticker", "").strip().upper()
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")

    if not (ticker and start_str and end_str):
        return render_template("backtest.html")

    try:
        start = dt.datetime.strptime(start_str, "%Y-%m-%d").date()
        end = dt.datetime.strptime(end_str, "%Y-%m-%d").date()
    except ValueError:
        return render_template("backtest.html", error="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD).",
                                ticker=ticker, start=start_str, end=end_str)
    if start >= end:
        return render_template("backtest.html", error="시작일은 종료일보다 빨라야 합니다.",
                                ticker=ticker, start=start_str, end=end_str)

    try:
        full_df = data.fetch_price_history_span(ticker, start, end)
        macro_bundle = data.fetch_macro_bundle_span(start, end)
    except Exception as e:
        return render_template("backtest.html", error=str(e), ticker=ticker, start=start_str, end=end_str)

    trading_days = [d for d in full_df.index if start <= d.date() <= end]
    if not trading_days:
        return render_template("backtest.html", error="해당 기간에 거래일 데이터가 없습니다.",
                                ticker=ticker, start=start_str, end=end_str)

    # 매일 계산하면 무겁고 표도 길어지므로 5거래일(약 1주) 간격으로 샘플링. 마지막 날은 항상 포함.
    sample_dates = trading_days[::5]
    if sample_dates[-1] != trading_days[-1]:
        sample_dates.append(trading_days[-1])

    timeline = []
    for d in sample_dates:
        sliced = full_df.loc[:d]
        if len(sliced) < 60:
            continue
        sliced_macro = {k: (v.loc[:d] if v is not None else None) for k, v in macro_bundle.items()}
        trend = signals.compute_trend_score(sliced)
        chase = signals.compute_chase_risk(sliced)
        regime = signals.compute_market_regime(sliced_macro)
        action = signals.decide_action(trend, chase, regime)
        timeline.append({"date": d.date().isoformat(), "price": float(sliced["Close"].iloc[-1]), "verdict": action.verdict})

    if not timeline:
        return render_template("backtest.html", error="신호를 계산하기엔 데이터가 부족한 기간입니다 (200일선 등에 필요한 과거 데이터 포함, 더 이후 시점으로 시도해보세요).",
                                ticker=ticker, start=start_str, end=end_str)

    # "시스템을 따랐다면" — ACCUMULATE 구간에서만 보유, 그 외에는 현금(단순화한 가정).
    system_return = 0.0
    holding = False
    entry_price = None
    for row in timeline:
        if row["verdict"] == "ACCUMULATE" and not holding:
            holding, entry_price = True, row["price"]
        elif row["verdict"] != "ACCUMULATE" and holding:
            system_return += (row["price"] - entry_price) / entry_price
            holding, entry_price = False, None
    if holding and entry_price:
        system_return += (timeline[-1]["price"] - entry_price) / entry_price

    buy_hold_pct = (timeline[-1]["price"] - timeline[0]["price"]) / timeline[0]["price"] * 100

    changes = [t for i, t in enumerate(timeline) if i > 0 and t["verdict"] != timeline[i - 1]["verdict"]]

    return render_template(
        "backtest.html", ticker=ticker, start=start_str, end=end_str,
        timeline=timeline, changes=changes,
        system_return_pct=round(system_return * 100, 1),
        buy_hold_pct=round(buy_hold_pct, 1),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
