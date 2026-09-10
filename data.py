"""
데이터 수집 레이어.

- 개별 종목 OHLCV: yfinance
- 매크로 지표(VIX, S&P500, 달러인덱스, WTI, 10년물 금리): yfinance

주의: 이 모듈은 실제 네트워크 호출(yfinance)을 포함합니다.
개발/테스트 환경에서 네트워크가 막혀 있을 경우 test_signals.py 의
합성 데이터(synthetic data)로 signals.py 로직만 검증하세요.
"""

from __future__ import annotations

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


def fetch_price_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """개별 종목의 일봉 OHLCV 데이터를 가져온다."""
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져오지 못했습니다. 티커를 확인해주세요.")
    df = df.dropna(subset=["Close"])
    return df


def fetch_macro_bundle(period: str = "1y") -> dict[str, pd.DataFrame]:
    """MARKET REGIME 계산에 필요한 매크로 지표들을 한 번에 가져온다."""
    bundle = {}
    for key, tk in MACRO_TICKERS.items():
        try:
            bundle[key] = fetch_price_history(tk, period=period)
        except Exception as e:
            # 매크로 지표 하나가 실패해도 전체가 죽지 않도록 None 처리
            bundle[key] = None
    return bundle
