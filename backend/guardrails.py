"""AI 호출 전에 실행되는 보안 필터 (ai.md 2장 구현)."""
import logging
import json
import os
import re
import urllib.request


log = logging.getLogger("guardrails")

MAX_INPUT_CHARS = 500

# ① 위기 상황 키워드 (정규식). 걸리면 LLM 호출을 막고 상담 안내만 반환한다.
CRISIS_PATTERNS = [
    r"죽고\s*싶", r"자살", r"자해", r"극단적\s*선택", r"목숨을?\s*끊", r"죽어버리고",
    r"살해", r"죽여\s*버리", r"해치고\s*싶",
    r"kill\s*myself", r"suicid", r"self[-\s]?harm",
]
_CRISIS_RE = re.compile("|".join(CRISIS_PATTERNS), re.IGNORECASE)

CRISIS_REPLY = (
    "많이 힘드신 것 같아 마음이 쓰여요. 지금 이야기해 주셔서 고마워요.\n\n"
    "📞 자살예방 상담전화 109 (24시간, 무료)\n"
    "📞 정신건강 위기상담 1577-0199\n"
    "긴박한 상황이라면 112 또는 119에 바로 연락해 주세요.\n\n"
    "혼자 견디지 않으셔도 괜찮아요. 전문 상담사가 곁에서 들어줄 거예요. "
    "스킨케어 상담은 마음이 조금 나아지신 뒤에 언제든 다시 도와드릴게요."
)


def is_crisis(text: str) -> bool:
    return bool(_CRISIS_RE.search(text))


def escalate_crisis(session_id: str) -> None:
    """관리자 채널 알림. 대화 내용은 보내지 않고 세션 ID만 기록한다."""
    log.warning("CRISIS keyword detected (session=%s)", session_id)
    webhook = os.getenv("CRISIS_WEBHOOK_URL")
    if not webhook:
        return
    try:
        body = json.dumps({"text": f"[샘 챗봇] 위기 키워드 감지 (session={session_id})"}).encode()
        req = urllib.request.Request(webhook, body, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        log.exception("crisis webhook failed")


# ③ 입력 정제: 제어문자 제거, 태그 제거, 길이 제한.
_TAG_RE = re.compile(r"<[^>]*>")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    return text.strip()[:MAX_INPUT_CHARS]
