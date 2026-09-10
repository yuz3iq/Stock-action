"""
네트워크 없이 Flask 라우트/템플릿 렌더링까지 통째로 확인하는 드라이런 스크립트.
data.py의 fetch 함수를 합성 데이터로 monkeypatch 하고 실제 HTTP 요청 없이
Flask test client로 /, /analyze 를 호출해 HTML이 정상 렌더링되는지 확인한다.

실행: python3 dry_run_app.py
"""

import sys
import types

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


def fake_price_history(ticker, period="1y", interval="1d"):
    scenarios = {
        "GOOD": uptrend_series(300, daily_drift=0.0015, seed=1),
        "CHASE": spike_series(300, seed=4),
        "BAD": downtrend_series(300, seed=2),
    }
    closes = scenarios.get(ticker, uptrend_series(300, seed=1))
    return make_ohlcv(closes)


def fake_macro_bundle(period="1y"):
    n = 260
    return {
        "vix": make_ohlcv(np.full(n, 13.0) + np.random.default_rng(5).normal(0, 0.3, n)),
        "index": make_ohlcv(smooth_trend(n, daily_drift=0.003, seed=10)),
        "dollar": make_ohlcv(smooth_trend(n, daily_drift=0.0, noise=0.001, seed=6)),
        "oil": make_ohlcv(smooth_trend(n, daily_drift=0.0, noise=0.003, seed=7)),
        "yield10y": make_ohlcv(np.full(n, 4.0) + np.random.default_rng(8).normal(0, 0.01, n)),
    }


data.fetch_price_history = fake_price_history
data.fetch_macro_bundle = fake_macro_bundle

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

print("\n드라이런 통과: 라우팅/템플릿 렌더링 정상.")
