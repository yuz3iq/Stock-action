"""
데이터 수집 레이어.

- 개별 종목 OHLCV: yfinance
- 매크로 지표(VIX, S&P500, 달러인덱스, WTI, 10년물 금리): yfinance

주의: 이 모듈은 실제 네트워크 호출(yfinance)을 포함합니다.
개발/테스트 환경에서 네트워크가 막혀 있을 경우 test_signals.py 의
합성 데이터(synthetic data)로 signals.py 로직만 검증하세요.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import yfinance as yf

# 매크로 지표 티커 매핑 (Yahoo Finance 기준)
MACRO_TICKERS = {
    "vix": "^VIX",        # 변동성 지수
    "index": "SPY",       # S&P500 추종 ETF (breadth/추세 프록시)
    "dollar": "DX-Y.NYB", # 달러 인덱스
    "oil": "CL=F",        # WTI 원유 선물
    "yield10y": "^TNX",   # 미국 10년물 국채금리
}


def fetch_price_history(ticker: str, period: str = "1y", interval: str = "1d",
                         as_of: "dt.date | None" = None) -> pd.DataFrame:
    """
    개별 종목의 일봉 OHLCV 데이터를 가져온다.

    as_of가 주어지면 "그 날짜 시점까지"의 데이터만 가져온다 (미래 데이터를 보지 않도록 —
    과거 시점 신호를 소급 계산할 때 미래 정보가 섞이면 안 되기 때문에 중요하다).
    yfinance의 end는 그 날짜를 포함하지 않으므로 as_of + 1일을 end로 넘긴다.
    """
    if as_of is not None:
        end = as_of + dt.timedelta(days=1)
        start = end - dt.timedelta(days=500)  # 200일선 + 여유분 확보
        df = yf.Ticker(ticker).history(start=start, end=end, interval=interval, auto_adjust=True)
    else:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져오지 못했습니다. 티커를 확인해주세요.")
    df = df.dropna(subset=["Close"])
    return df


def fetch_macro_bundle(period: str = "1y", as_of: "dt.date | None" = None) -> dict[str, pd.DataFrame]:
    """MARKET REGIME 계산에 필요한 매크로 지표들을 한 번에 가져온다."""
    bundle = {}
    for key, tk in MACRO_TICKERS.items():
        try:
            bundle[key] = fetch_price_history(tk, period=period, as_of=as_of)
        except Exception:
            # 매크로 지표 하나가 실패해도 전체가 죽지 않도록 None 처리
            bundle[key] = None
    return bundle


def fetch_price_history_span(ticker: str, start: dt.date, end: dt.date, interval: str = "1d") -> pd.DataFrame:
    """
    [start, end] 구간 전체를 신호 계산에 필요한 lookback(500일)까지 포함해서 "한 번만" 가져온다.

    백테스트처럼 구간 내 여러 날짜마다 그 시점 기준 신호를 계산해야 할 때, fetch_price_history의
    as_of 파라미터를 날짜 수만큼 반복 호출하면 네트워크 호출이 N번 발생해서 느리다. 대신 이 함수로
    한 번에 넉넉히 받아온 뒤, 호출하는 쪽에서 df.loc[:특정날짜]로 슬라이스해서 재사용한다.
    """
    buffer_start = start - dt.timedelta(days=500)
    fetch_end = end + dt.timedelta(days=1)
    df = yf.Ticker(ticker).history(start=buffer_start, end=fetch_end, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져오지 못했습니다. 티커나 기간을 확인해주세요.")
    return df.dropna(subset=["Close"])


def fetch_macro_bundle_span(start: dt.date, end: dt.date) -> dict[str, pd.DataFrame]:
    """fetch_price_history_span의 매크로 버전. 백테스트에서 구간 전체를 한 번에 받아 슬라이스용으로 쓴다."""
    bundle = {}
    for key, tk in MACRO_TICKERS.items():
        try:
            bundle[key] = fetch_price_history_span(tk, start, end)
        except Exception:
            bundle[key] = None
    return bundle
