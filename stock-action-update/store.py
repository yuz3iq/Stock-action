"""
아주 단순한 JSON 파일 기반 저장소.

개인 전용 도구라서 진짜 데이터베이스는 과함 — 로컬 파일 하나에 다 저장한다.
저장 내용:
  - portfolio_settings: Portfolio Plan에서 마지막으로 쓴 관심종목/자본금/위험성향
    (매번 다시 입력 안 해도 되게)
  - trades: 사용자가 입력한 실제 매매 기록 (내 매매 vs 시스템 권장 비교용)

주의: 이 파일(local_store.json)은 개인 매매 기록이 들어갈 수 있어서 .gitignore에 넣어
공개 GitHub 저장소에 올라가지 않게 한다. Render 재배포 시에는 초기화된다(디스크가 코드와
함께 새로 만들어지므로) — 개인용 MVP 단계에서는 감수하는 트레이드오프.

  - snapshots: 관심종목(Portfolio Plan에 등록된 티커)별로 하루에 한 번씩 그날의 판단
    (ACCUMULATE/WAIT/... 등)을 기록해두는 이력. "판단이 바뀌는 순간 감지" 기능의 기반 데이터.
    같은 날짜에는 한 번만 기록하고(중복 방지), 종목당 최근 120개까지만 보관한다.
"""

from __future__ import annotations

import json
import os
import uuid

STORE_PATH = os.path.join(os.path.dirname(__file__), "local_store.json")

_DEFAULT = {
    "portfolio_settings": {
        "tickers": "AAPL,MSFT,NVDA,GOOGL,AMZN,TSLA,META,JPM,XOM,JNJ",
        "capital": 30000000,
        "risk": "Moderate",
    },
    "trades": [],
    "snapshots": {},
}


def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return json.loads(json.dumps(_DEFAULT))
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, val in _DEFAULT.items():
            data.setdefault(key, val)
        return data
    except Exception:
        return json.loads(json.dumps(_DEFAULT))


def _save(data: dict) -> None:
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_portfolio_settings() -> dict:
    return _load()["portfolio_settings"]


def set_portfolio_settings(tickers: str, capital: int, risk: str) -> None:
    data = _load()
    data["portfolio_settings"] = {"tickers": tickers, "capital": capital, "risk": risk}
    _save(data)


def get_trades() -> list[dict]:
    return _load()["trades"]


def add_trade(ticker: str, date: str, price: float, quantity: float, side: str) -> dict:
    data = _load()
    trade = {
        "id": uuid.uuid4().hex[:8],
        "ticker": ticker,
        "date": date,       # "YYYY-MM-DD"
        "price": price,
        "quantity": quantity,
        "side": side,        # "BUY" or "SELL"
    }
    data["trades"].append(trade)
    _save(data)
    return trade


def delete_trade(trade_id: str) -> None:
    data = _load()
    data["trades"] = [t for t in data["trades"] if t["id"] != trade_id]
    _save(data)


def get_watchlist_tickers() -> list[str]:
    """Portfolio Plan에 등록된 관심종목 = 판단 변화를 추적할 종목 목록."""
    raw = get_portfolio_settings()["tickers"]
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def get_snapshots(ticker: str) -> list[dict]:
    return _load()["snapshots"].get(ticker, [])


def get_all_snapshots() -> dict:
    return _load().get("snapshots", {})


def record_snapshot(ticker: str, date: str, verdict: str, trend: str, chase: str,
                     regime: str, max_history: int = 120) -> bool:
    """
    오늘자 판단을 기록한다. 같은 날짜 기록이 이미 있으면 아무것도 하지 않는다
    (하루에 한 번만 기록 — 장중에 여러 번 방문해도 스냅샷이 중복되지 않게).
    Returns: 새로 기록했으면 True, 이미 그날 기록이 있어서 건너뛰었으면 False.
    """
    data = _load()
    data.setdefault("snapshots", {})
    history = data["snapshots"].setdefault(ticker, [])
    if history and history[-1]["date"] == date:
        return False
    history.append({"date": date, "verdict": verdict, "trend": trend, "chase": chase, "regime": regime})
    if len(history) > max_history:
        del history[: len(history) - max_history]
    _save(data)
    return True


def get_changes_today(today: str) -> list[dict]:
    """오늘 날짜로 기록된 스냅샷 중, 바로 전 스냅샷과 판단(verdict)이 달라진 종목만 골라낸다.
    (네트워크 호출 없이 이미 저장된 스냅샷만 보는 가벼운 조회 — 홈 화면 배너용)"""
    data = _load()
    out = []
    for ticker, history in data.get("snapshots", {}).items():
        if len(history) >= 2 and history[-1]["date"] == today and history[-2]["verdict"] != history[-1]["verdict"]:
            out.append({"ticker": ticker, "from": history[-2]["verdict"], "to": history[-1]["verdict"]})
    return out
