# Stock Action Dashboard (MVP)

티커를 입력하면 **TREND SCORE / CHASE RISK / MARKET REGIME** 세 신호를 계산하고,
세 신호가 일치하는지 충돌하는지에 따라 `ACCUMULATE / WAIT / WAIT FOR PULLBACK / WATCH / AVOID`
중 하나로 판단을 정리해주는 웹 대시보드 프로토타입입니다.

## 왜 이 세 개만 있나요

기획서에는 RECOVERY SCORE, ACTION ENGINE(자본 배분), IF I'M WRONG(포지션 관리) 등이
더 있지만, 이번 MVP는 "충돌하면 WAIT"라는 핵심 판단 로직이 실제로 말이 되는지부터
검증하기 위해 범위를 의도적으로 좁혔습니다. 자본금·비중(%) 계산은 투자자문업 관련
리스크가 있어 이 단계에서는 다루지 않습니다.

MARKET REGIME은 VIX·지수·달러·유가·금리를 규칙 기반으로 조합한 것으로, "새로운 고유
지표"라고 내세우지 않습니다 (계산 로직은 `signals.py`의 `compute_market_regime`에
그대로 노출되어 있습니다). 차별점은 지표 자체가 아니라 세 신호가 충돌할 때 무엇을
하라고 말해주는지(`decide_action`)에 있습니다.

## 실행 방법

```bash
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://127.0.0.1:5000` 접속 후 티커(예: NVDA, AAPL) 입력.

## 계산 로직만 빠르게 검증하고 싶다면 (네트워크 불필요)

```bash
python test_signals.py
```

합성(가짜) 가격 데이터로 우상향/하락/급등 등 시나리오를 만들어 TREND / CHASE RISK /
MARKET REGIME / ACTION 로직이 기대한 라벨을 내는지 확인합니다. 실제 개발 환경에
야후 파이낸스 접속이 막혀 있어서, 이 프로젝트는 이 스크립트로 로직을 검증했고
실제 네트워크 호출(yfinance)은 로컬 실행 환경에서 확인해주셔야 합니다.

## 파일 구조

- `app.py` — Flask 라우팅, 템플릿 렌더링
- `data.py` — yfinance로 종목/매크로 데이터 조회
- `signals.py` — TREND SCORE / CHASE RISK / MARKET REGIME / ACTION 계산 로직 (핵심)
- `templates/` — 입력 폼(`index.html`), 결과 화면(`result.html`)
- `test_signals.py` — 합성 데이터 기반 로직 검증

## Portfolio Plan (`/portfolio`)

관심종목을 쉼표로 구분해 여러 개(개수 제한 없음) 입력하면, 각 종목의 실제 시세를 야후
파이낸스에서 받아 TREND / CHASE RISK / ACTION을 계산하고, 자본금 + 위험성향(Conservative/
Moderate/Aggressive)에 따라 종목별 비중(%)과 금액(₩)까지 계산합니다. 개인 전용 사용을
전제로 하기 때문에 구체적인 금액을 그대로 보여줍니다 (자본시장법상 유사투자자문업은
"불특정 다수 대상"이 요건이라 개인 혼자 쓰는 도구에는 해당하지 않습니다 — 단, 다른 사람도
쓸 수 있게 공개/배포하는 순간부터는 이 판단이 달라지니 주의).

비중 로직은 `signals.py`의 `RISK_PROFILES`, `weight_for_action`, `build_portfolio_plan`에
그대로 있습니다: ACCUMULATE만 비중을 받고(STRONG BULLISH 15%, BULLISH 8%), 위험성향×
시장환경으로 정해지는 "최대 주식비중 상한"을 넘으면 비례 축소, 남는 건 전부 현금.

## 다음 단계 (2단계 후보)

- RECOVERY SCORE (하락 후 회복 조건 판단)
- 포지션 입력(평단가·보유비중) 기반 개인화 — 단, 구체적 금액/비중 추천은 법률 검토 후
- 실제 과거 사례 백테스트 (예: "이 신호가 2022년 하락장에서 뭘 잡아냈는가")
