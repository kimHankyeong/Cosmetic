"""화장품 추천 챗봇 '샘' API.  실행: uvicorn main:app --reload --port 8000"""
import json
import logging
import os
import secrets
import threading
import time
from typing import Iterator

import groq
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

import agent  # noqa: E402  (LLM_MODEL 등 환경변수를 읽으므로 load_dotenv 이후 import)
import catalog  # noqa: E402
import guardrails  # noqa: E402

logging.basicConfig(level=logging.INFO)

SESSION_TTL_SEC = 30 * 60  # ai.md: 30분 후 메모리에서 파기
MAX_TURNS_PER_SESSION = 30

app = FastAPI(title="샘 - 화장품 추천 AI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    # Vercel 미리보기 배포 주소(cosmetic-xxxx-clarakim.vercel.app)도 허용
    allow_origin_regex=r"https://cosmetic-[a-z0-9]+-clarakim\.vercel\.app",
    allow_methods=["POST", "DELETE"],
    allow_headers=["Content-Type"],
)

client = groq.Groq(api_key=os.getenv("GROQ_API_KEY") or "missing")


class Session:
    """대화 기록, 피부 프로필 모두 메모리에만 두고 TTL 후 파기한다."""

    def __init__(self) -> None:
        self.history: list[dict] = []
        self.profile: dict = {}
        self.seen_ids: set[int] = set()
        self.turns = 0
        self.last_active = time.time()
        self.lock = threading.Lock()


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


def _get_session(session_id: str | None) -> tuple[str, Session]:
    now = time.time()
    with _sessions_lock:
        for sid in [s for s, v in _sessions.items() if now - v.last_active > SESSION_TTL_SEC]:
            del _sessions[sid]
        if session_id and session_id in _sessions:
            return session_id, _sessions[session_id]
        sid = secrets.token_urlsafe(16)
        _sessions[sid] = Session()
        return sid, _sessions[sid]


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(max_length=2000)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stream_chat(sid: str, session: Session, text: str) -> Iterator[str]:
    yield _sse({"type": "session", "session_id": sid})

    # 위기 키워드: LLM 호출 없이 즉시 안내 (대화 내용은 저장하지 않는다)
    if guardrails.is_crisis(text):
        guardrails.escalate_crisis(sid)
        yield _sse({"type": "crisis", "text": guardrails.CRISIS_REPLY})
        yield _sse({"type": "done"})
        return

    with session.lock:
        if session.turns >= MAX_TURNS_PER_SESSION:
            yield _sse({"type": "error", "text": "한 상담에서 나눌 수 있는 대화 수를 넘었어요. 새로 시작해 주세요."})
            return
        checkpoint = len(session.history)
        session.history.append({"role": "user", "content": text})
        completed = False
        try:
            for ev in agent.chat_turn_stream(client, session.history, session.profile, session.seen_ids):
                if ev["type"] == "products":
                    cards = [catalog.get_product(i) for i in ev["ids"]]
                    ev = {"type": "products", "products": [
                        {"id": p["id"], "name": p["name"], "price": p["price"], "rating": p["rating"],
                         "source": p["source"], "brand": p["brand"], "url": p["url"]}
                        for p in cards if p
                    ]}
                yield _sse(ev)
            completed = True
            session.turns += 1
            session.last_active = time.time()
        except groq.GroqError:
            logging.exception("LLM API error")
            yield _sse({"type": "error", "text": "지금 상담이 원활하지 않아요. 잠시 후 다시 시도해 주세요."})
            return
        finally:
            if not completed:  # 오류나 연결 끊김: 미완성 턴은 기록에서 제거
                del session.history[checkpoint:]
    yield _sse({"type": "done"})


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    text = guardrails.sanitize(req.message)
    if not text:
        raise HTTPException(400, "메시지가 비어 있어요.")
    sid, session = _get_session(req.session_id)
    return StreamingResponse(
        _stream_chat(sid, session, text),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/chat/{session_id}", status_code=204)
def end_session(session_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(session_id, None)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "model": agent.MODEL, "products": catalog.count(), "has_key": bool(os.getenv("GROQ_API_KEY"))}
