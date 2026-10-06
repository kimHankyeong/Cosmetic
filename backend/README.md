# 샘 - 화장품 추천 AI 백엔드

자유 대화로 피부 고민을 듣고, 상품 DB에서 골라 이유와 함께 추천하는 챗봇 API (FastAPI + Groq 무료 티어). 응답은 SSE로 스트리밍돼요.

## 실행

```powershell
cd 코스메틱back
copy .env.example .env      # GROQ_API_KEY 입력 (https://console.groq.com/keys 에서 무료 발급)
.venv\Scripts\python -m uvicorn main:app --reload --port 8000
```

그다음 `코스메틱프론트/index.html`을 브라우저로 열고 우측 챗봇 버튼을 누르면 돼요.

## 구조

| 파일 | 역할 |
|---|---|
| `main.py` | `/api/chat` SSE 엔드포인트, 인메모리 세션(30분 TTL, 최대 30턴) |
| `agent.py` | 시스템 프롬프트('샘'), 도구 호출 루프, 토큰 스트리밍, 피부 프로필 기억 |
| `catalog.py` | SQLite 상품 DB 조회 (`search_products`, `get_product`) |
| `guardrails.py` | 위기 키워드 차단(109 안내) · 입력 정제 · 관리자 알림 (ai.md 구현) |
| `data/products.json` | 상품 원본 (프론트 `PRODUCTS`에서 추출한 64개) |
| `data/cosmetic.db` | 첫 실행 때 products.json으로 자동 생성되는 DB |

## 참고

- 모델은 `.env`의 `LLM_MODEL`로 바꿀 수 있어요 (기본 `openai/gpt-oss-120b`).
- 상품을 실제 데이터로 바꾸려면 `data/products.json`을 교체하고 `data/cosmetic.db`를 지운 뒤 서버를 다시 켜세요. 프론트 `PRODUCTS`와 id가 같아야 추천 카드가 표시돼요.
- 피부 프로필(피부 타입, 고민, 기피 성분, 예산)은 세션 메모리에만 저장되고 30분 뒤 사라져요.
- 위기 알림을 Slack 등으로 받으려면 `CRISIS_WEBHOOK_URL`을 설정하세요.
- Groq 무료 티어는 분당 요청 수 제한이 있어서, 한도를 넘으면 "상담이 원활하지 않아요" 메시지가 나와요.
