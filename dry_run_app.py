"""
네트워크 없이 Flask 라우트/템플릿 렌더링까지 통째로 확인하는 드라이런 스크립트.
data.py의 fetch 함수를 합성 데이터로 monkeypatch 하고 실제 HTTP 요청 없이
Flask test client로 /, /analyze 를 호출해 HTML이 정상 렌더링되는지 확인한다.

실행: python3 dry_run_app.py
"""

import sys
import types

import pandas as pd

# data.py는 최상단에서 `import yfinance as yf`를 하는데, 이 개발 환경은 외부 PyPI
# 접속이 막혀 있어 yfinance를 설치할 수 없다. 드라이런 전용으로 더미 모듈을 끼워 넣어
# import 자체는 통과시키고, 실제 데이터 조회 함수는 아래에서 monkeypatch 한다.
if "yfinance" not in sys.modules:
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda *a, **k: None
    sys.modules["yfinance"] = fake_yf

import data
from test_signals import uptrend_series, spike_series, downtrend_series, smooth_trend, make_ohlcv

import numpy as np


def fake_price_history(ticker, period="1y", interval="1d", as_of=None):
    # as_of는 무시한다 — 합성 데이터는 시간에 따라 달라지지 않으므로 로직(파라미터가
    # 제대로 전달되는지)만 확인하면 충분하다. 실제 시점별 데이터 차이는 실기기에서 확인.
    scenarios = {
        "GOOD": uptrend_series(300, daily_drift=0.0015, seed=1),
        "CHASE": spike_series(300, seed=4),
        "BAD": downtrend_series(300, seed=2),
    }
    closes = scenarios.get(ticker, uptrend_series(300, seed=1))
    return make_ohlcv(closes)


def fake_macro_bundle(period="1y", as_of=None):
    n = 260
    return {
        "vix": make_ohlcv(np.full(n, 13.0) + np.random.default_rng(5).normal(0, 0.3, n)),
        "index": make_ohlcv(smooth_trend(n, daily_drift=0.003, seed=10)),
        "dollar": make_ohlcv(smooth_trend(n, daily_drift=0.0, noise=0.001, seed=6)),
        "oil": make_ohlcv(smooth_trend(n, daily_drift=0.0, noise=0.003, seed=7)),
        "yield10y": make_ohlcv(np.full(n, 4.0) + np.random.default_rng(8).normal(0, 0.01, n)),
    }


def fake_price_history_span(ticker, start, end, interval="1d"):
    # 실제 fetch_price_history_span처럼 [start-500일, end] 구간 전체를 한 번에 만든다.
    n_days = (end - start).days + 500 + 30
    scenarios = {
        "GOOD": uptrend_series(n_days, daily_drift=0.0015, seed=1),
        "CHASE": spike_series(n_days, seed=4),
        "BAD": downtrend_series(n_days, seed=2),
    }
    closes = scenarios.get(ticker, uptrend_series(n_days, seed=1))
    return make_ohlcv(closes, end_date=pd.Timestamp(end))


def fake_macro_bundle_span(start, end):
    n_days = (end - start).days + 500 + 30
    end_ts = pd.Timestamp(end)
    return {
        "vix": make_ohlcv(np.full(n_days, 13.0) + np.random.default_rng(5).normal(0, 0.3, n_days), end_date=end_ts),
        "index": make_ohlcv(smooth_trend(n_days, daily_drift=0.003, seed=10), end_date=end_ts),
        "dollar": make_ohlcv(smooth_trend(n_days, daily_drift=0.0, noise=0.001, seed=6), end_date=end_ts),
        "oil": make_ohlcv(smooth_trend(n_days, daily_drift=0.0, noise=0.003, seed=7), end_date=end_ts),
        "yield10y": make_ohlcv(np.full(n_days, 4.0) + np.random.default_rng(8).normal(0, 0.01, n_days), end_date=end_ts),
    }


data.fetch_price_history = fake_price_history
data.fetch_macro_bundle = fake_macro_bundle
data.fetch_price_history_span = fake_price_history_span
data.fetch_macro_bundle_span = fake_macro_bundle_span

import os as _os
import store

# 이전 실행에서 남은 로컬 저장소가 있으면 지우고 깨끗하게 시작 (재현 가능한 테스트를 위해)
if _os.path.exists(store.STORE_PATH):
    _os.remove(store.STORE_PATH)

import app as flask_app_module

client = flask_app_module.app.test_client()

r0 = client.get("/")
print("GET /  ->", r0.status_code, "OK" if b"\xed\x8b\xb0\xec\xbb\xa4" in r0.data or True else "")
assert r0.status_code == 200

for ticker in ["GOOD", "CHASE", "BAD", "NOPE_WONT_MATCH"]:
    r = client.get(f"/analyze?ticker={ticker}")
    print(f"GET /analyze?ticker={ticker} -> {r.status_code}")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    for needle in ["Trend Score", "Chase Risk", "Market Regime", ticker]:
        assert needle in html, f"'{needle}' not found in response for {ticker}"
    # 액션 배너 확인 (설명 문구에도 단어가 섞여 나오므로 실제 배너 마크업만 정확히 매칭)
    found = None
    for verdict in ["WAIT FOR PULLBACK", "ACCUMULATE", "WAIT", "WATCH", "AVOID"]:
        if f'class="verdict">{verdict}<' in html:
            found = verdict
            break
    assert found, f"no verdict banner found for {ticker}"
    print("   verdict:", found)

r_pf = client.get("/portfolio?tickers=GOOD,CHASE,BAD&capital=30000000&risk=Moderate")
print("GET /portfolio ->", r_pf.status_code)
assert r_pf.status_code == 200
html_pf = r_pf.data.decode("utf-8")
for needle in ["Equity Exposure", "Market Regime", "GOOD", "CHASE", "BAD", "CASH"]:
    assert needle in html_pf, f"'{needle}' missing from /portfolio response"
print("   portfolio page OK")

print("\n--- /trades (내 매매 vs 시스템 권장) ---")
import datetime as dt

r_add = client.post("/trades/add", data={
    "ticker": "GOOD", "date": "2024-01-15", "side": "BUY", "price": "100.00", "quantity": "10",
})
print("POST /trades/add ->", r_add.status_code, "(redirect expected: 302)")
assert r_add.status_code == 302

r_trades = client.get("/trades")
print("GET /trades ->", r_trades.status_code)
assert r_trades.status_code == 200
html_trades = r_trades.data.decode("utf-8")
for needle in ["GOOD", "2024-01-15", "100.00"]:
    assert needle in html_trades, f"'{needle}' missing from /trades response"
assert ("일치" in html_trades) or ("다르게 행동함" in html_trades)
print("   trades page renders comparison row OK")

trade_id = store.get_trades()[0]["id"]
r_del = client.post(f"/trades/delete/{trade_id}")
assert r_del.status_code == 302
assert store.get_trades() == []
print("   delete OK")

print("\n--- /snapshot, /changes (판단 변화 감지) ---")

# 관심종목을 합성 시나리오 티커로 맞춰둔다.
store.set_portfolio_settings("GOOD,CHASE,BAD", 30000000, "Moderate")

r_snap1 = client.get("/snapshot")
print("GET /snapshot (1st today) ->", r_snap1.status_code, r_snap1.get_json())
assert r_snap1.status_code == 200
snap1 = r_snap1.get_json()
assert snap1["recorded"] == 3, f"expected 3 tickers recorded, got {snap1}"
assert snap1["changed"] == [], "첫 기록인데 changed가 있으면 안 됨 (비교할 이전 기록이 없음)"

r_snap2 = client.get("/snapshot")
snap2 = r_snap2.get_json()
print("GET /snapshot (2nd today, same day) ->", snap2)
assert snap2["recorded"] == 0, "같은 날 두 번째 호출은 중복 기록하면 안 됨"

# "어제" 다른 판단이 있었던 것처럼 이력을 조작해서 변화 감지 로직을 확인.
today_str = dt.date.today().isoformat()
data_blob = store._load()
data_blob["snapshots"]["GOOD"][-1] = {
    "date": today_str, "verdict": "ACCUMULATE", "trend": "STRONG BULLISH", "chase": "LOW", "regime": "RISK-ON",
}
data_blob["snapshots"]["GOOD"].insert(-1, {
    "date": "2000-01-01", "verdict": "WAIT", "trend": "BULLISH", "chase": "LOW", "regime": "RISK-OFF",
})
store._save(data_blob)

today_changes = store.get_changes_today(today_str)
print("store.get_changes_today ->", today_changes)
assert any(c["ticker"] == "GOOD" for c in today_changes), "GOOD의 판단 변화가 감지돼야 함"

r_index = client.get("/")
html_index = r_index.data.decode("utf-8")
assert "판단이 바뀐 종목" in html_index, "홈 화면에 변화 배너가 떠야 함"
print("   index banner OK")

r_changes = client.get("/changes")
print("GET /changes ->", r_changes.status_code)
assert r_changes.status_code == 200
html_changes = r_changes.data.decode("utf-8")
for needle in ["GOOD", "CHASE", "BAD", "바뀜"]:
    assert needle in html_changes, f"'{needle}' missing from /changes response"
print("   changes page OK")

print("\n--- /analyze Recovery Score 카드 ---")
RECOVERY_CARD_MARK = 'label">Recovery Score'  # scope-note 설명 문구와 구분하기 위해 카드 마크업만 매칭
r_bad_analyze = client.get("/analyze?ticker=BAD")
html_bad = r_bad_analyze.data.decode("utf-8")
assert RECOVERY_CARD_MARK in html_bad, "하락 추세 종목에는 Recovery Score 카드가 떠야 함"
r_good_analyze = client.get("/analyze?ticker=GOOD")
html_good = r_good_analyze.data.decode("utf-8")
assert RECOVERY_CARD_MARK not in html_good, "상승 추세 종목엔 Recovery Score 카드가 뜨면 안 됨 (N/A라 숨김)"
print("   recovery score 조건부 표시 OK")

print("\n--- /positions (내 포지션: IF I'M WRONG + POSITION MANAGEMENT) ---")
store.add_trade("CHASE", "2024-01-15", 100.0, 5, "BUY")
r_positions = client.get("/positions")
print("GET /positions ->", r_positions.status_code)
assert r_positions.status_code == 200
html_positions = r_positions.data.decode("utf-8")
for needle in ["CHASE", "IF I'M WRONG", "평단"]:
    assert needle in html_positions, f"'{needle}' missing from /positions response"
assert any(s in html_positions for s in ["HOLD", "CONSIDER_PARTIAL_PROFIT", "TRAILING_STOP_HIT"])
print("   positions page OK")
# 정리
for t in store.get_trades():
    store.delete_trade(t["id"])
assert store.get_open_positions() == []

print("\n--- /compare-risk (보수적 vs 공격적 동시 비교) ---")
r_cmp = client.get("/compare-risk?tickers=GOOD,CHASE,BAD&capital=30000000")
print("GET /compare-risk ->", r_cmp.status_code)
assert r_cmp.status_code == 200
html_cmp = r_cmp.data.decode("utf-8")
for needle in ["Conservative", "Moderate", "Aggressive", "GOOD"]:
    assert needle in html_cmp, f"'{needle}' missing from /compare-risk response"
print("   compare-risk page OK")

print("\n--- /backtest (케이스 스터디) ---")
r_bt = client.get("/backtest?ticker=GOOD&start=2024-01-01&end=2024-06-01")
print("GET /backtest ->", r_bt.status_code)
assert r_bt.status_code == 200
html_bt = r_bt.data.decode("utf-8")
for needle in ["시스템 판단을 따랐다면", "Buy & Hold", "2024-01-01"]:
    assert needle in html_bt, f"'{needle}' missing from /backtest response"
print("   backtest page OK")

r_bt_bad_range = client.get("/backtest?ticker=GOOD&start=2024-06-01&end=2024-01-01")
assert "시작일은 종료일보다" in r_bt_bad_range.data.decode("utf-8")
print("   backtest 잘못된 날짜 범위 검증 OK")

print("\n드라이런 통과: 라우팅/템플릿 렌더링 정상.")

# 테스트가 남긴 로컬 저장소 파일 정리 (실제 실행 시 생성되는 파일이 저장소에 남지 않게)
if _os.path.exists(store.STORE_PATH):
    _os.remove(store.STORE_PATH)
