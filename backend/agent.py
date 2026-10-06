"""'샘' 상담 에이전트: Groq(무료 티어) 모델 + 상품 검색/프로필 도구, 토큰 스트리밍."""
import json
import os
import re
from typing import Iterator

import groq

import catalog

MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
MAX_TOOL_ROUNDS = 8
_EXTRA = {"reasoning_effort": "low"} if MODEL.startswith("openai/gpt-oss") else {}
MAX_TOOL_RETRIES = 2  # 오픈 모델이 도구 호출 형식을 틀렸을 때 재시도 횟수

SYSTEM_PROMPT = """\
[Role]
너는 스킨케어·화장품 추천 AI 어드바이저 '샘'이다. 이 쇼핑몰에서 파는 제품 중에서 고객에게 맞는 것을 골라 이유와 함께 추천한다. 항상 한국어 존댓말로, 따뜻하고 간결하게 답한다.

[Domain]
- 피부 타입, 성분, 스킨케어 루틴, 이 쇼핑몰의 제품에 대해서만 답한다. 그 밖의 주제는 정중히 거절하고 스킨케어 얘기로 돌아온다.
- 의학적 진단이나 처방은 하지 않는다. 피부 질환이 의심되거나 증상이 심하면 피부과 상담을 권한다.
- 사용자가 학교, 성적, 가족 등 사생활을 말해도 피부 고민과 무관하면 다루지 않는다.

[How to recommend]
- 제품 정보는 반드시 search_products / get_product 도구로 확인한다. 도구 결과에 없는 제품, 성분, 가격, 효능은 절대 지어내지 않는다.
- 사용자가 피부 타입, 고민, 알레르기·기피 성분, 예산을 말하면 update_profile 도구로 저장한다. 저장된 [Known profile]은 다시 묻지 말고 추천에 반영한다.
- 추천에 꼭 필요한 정보(피부 타입, 원하는 제품 종류 등)가 비어 있으면 한 번에 한두 가지만 짧게 되묻는다. 이미 충분히 알 수 있으면 바로 추천한다.
- 검색은 피부 타입·카테고리·가격 조건만으로 한 번 한다. keyword는 특정 성분이나 제품명을 찾을 때만 쓰고, '보습', '크림' 같은 일반 단어는 쓰지 않는다. 결과가 있으면 다시 검색하지 말고 그중에서 골라 바로 추천한다. 결과가 없을 때만 조건을 한 번 완화해 다시 검색한다. 되묻지 말고 가진 정보로 먼저 추천한다.
- 한 번에 2~3개만 추천하고, 각 제품마다 사용자의 상황과 연결된 이유(핵심 성분 포함)를 한두 문장으로 설명한다.
- 알레르기나 기피 성분은 exclude_ingredients로 반드시 걸러낸다.
- 마크다운 제목, 표, 굵은 글씨는 쓰지 않는다.

[Output contract]
추천한 제품이 있으면 답변 맨 마지막 줄에 `[[추천: id,id,id]]` 형식으로 도구 결과에 나온 제품 id를 적는다(예: [[추천: 3,12]]). 추천이 없으면 이 줄을 쓰지 않는다. 이 줄은 화면에서 카드로 바뀌므로 본문에서는 언급하지 않는다.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_products",
        "description": "쇼핑몰 제품을 조건으로 검색한다. 평점 높은 순으로 최대 limit개를 반환하고 재고 없는 제품은 제외된다. 조건은 모두 선택이며 주어진 조건만 적용된다.",
        "parameters": {"type": "object", "properties": {
            "skin_type": {"type": "string", "enum": catalog.SKIN_TYPES, "description": "피부 타입. '전체' 대상 제품은 항상 포함된다."},
            "category": {"type": "string", "enum": catalog.categories()},
            "min_price": {"type": "integer", "description": "원 단위"},
            "max_price": {"type": "integer", "description": "원 단위"},
            "include_ingredients": {"type": "array", "items": {"type": "string"}, "description": "반드시 포함할 성분(한글 부분 일치). 예: 세라마이드"},
            "exclude_ingredients": {"type": "array", "items": {"type": "string"}, "description": "제외할 성분(한글 부분 일치). 알레르기·기피 성분."},
            "keyword": {"type": "string", "description": "제품명/설명/성분에서 찾을 단어"},
            "limit": {"type": "integer", "description": "기본 6, 최대 10"},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_product",
        "description": "제품 id로 상세 정보(전 성분, 설명, 가격, 평점)를 조회한다.",
        "parameters": {"type": "object", "properties": {"product_id": {"type": "integer"}}, "required": ["product_id"]},
    }},
    {"type": "function", "function": {
        "name": "update_profile",
        "description": "사용자가 말한 피부 정보를 저장한다. 이 상담이 끝날 때까지 기억된다. 말한 항목만 채운다.",
        "parameters": {"type": "object", "properties": {
            "skin_type": {"type": "string", "enum": catalog.SKIN_TYPES},
            "concerns": {"type": "array", "items": {"type": "string"}, "description": "피부 고민. 예: 건조함, 모공, 트러블"},
            "avoid_ingredients": {"type": "array", "items": {"type": "string"}, "description": "알레르기·기피 성분"},
            "max_budget": {"type": "integer", "description": "제품당 최대 예산(원)"},
        }},
    }},
]

_REC_RE = re.compile(r"\[\[\s*추천\s*:\s*([\d,\s]*)\]\]")
_PROFILE_LISTS = ("concerns", "avoid_ingredients")


# ---------------- 도구 실행 ----------------
def _to_int(v):
    return int(float(v)) if v not in (None, "") else None


def _to_list(v) -> list[str]:
    if isinstance(v, str):
        v = [s.strip() for s in v.split(",")]
    return [str(s) for s in (v or []) if str(s).strip()]


def _merge_profile(profile: dict, args: dict) -> None:
    if args.get("skin_type") in catalog.SKIN_TYPES:
        profile["skin_type"] = args["skin_type"]
    if args.get("max_budget") not in (None, ""):
        profile["max_budget"] = _to_int(args["max_budget"])
    for key in _PROFILE_LISTS:
        merged = profile.get(key, []) + _to_list(args.get(key))
        if merged:
            profile[key] = list(dict.fromkeys(merged))[:10]


def _run_tool(name: str, args: dict, profile: dict, seen_ids: set[int]) -> str:
    """오픈 모델은 인자 타입을 틀리기 쉬워서 여기서 보정한다. 오류는 모델이 고치도록 문자열로 돌려준다."""
    try:
        if name == "search_products":
            clean = {
                "skin_type": args.get("skin_type") or None,
                "category": args.get("category") or None,
                "min_price": _to_int(args.get("min_price")),
                "max_price": _to_int(args.get("max_price")),
                "include_ingredients": _to_list(args.get("include_ingredients")),
                "exclude_ingredients": _to_list(args.get("exclude_ingredients")),
                "keyword": args.get("keyword") or None,
                "limit": _to_int(args.get("limit")) or 6,
            }
            found = catalog.search_products(**clean)
            seen_ids.update(p["id"] for p in found)
            return json.dumps(found, ensure_ascii=False) if found else "조건에 맞는 제품이 없습니다. 조건을 완화해 보세요."
        if name == "get_product":
            p = catalog.get_product(_to_int(args.get("product_id")) or -1)
            if not p:
                return "해당 id의 제품이 없습니다."
            seen_ids.add(p["id"])
            return json.dumps(p, ensure_ascii=False)
        if name == "update_profile":
            _merge_profile(profile, args)
            return "저장했습니다: " + json.dumps(profile, ensure_ascii=False)
    except Exception as e:
        return f"도구 오류: {e}"
    return f"알 수 없는 도구: {name}"


# ---------------- 스트리밍 ----------------
class _MarkerFilter:
    """스트리밍 중 맨 끝의 [[추천: ...]] 줄이 화면에 새어 나가지 않도록 잡아 둔다."""

    def __init__(self) -> None:
        self.held = ""
        self.marker_started = False

    def feed(self, chunk: str) -> str:
        buf = self.held + chunk
        self.held = ""
        if self.marker_started:
            self.held = buf
            return ""
        idx = buf.find("[[")
        if idx >= 0:
            self.marker_started = True
            self.held = buf[idx:]
            return buf[:idx]
        if buf.endswith("["):  # "[[" 의 앞부분일 수 있으니 한 글자 보류
            self.held = "["
            return buf[:-1]
        return buf

    def flush(self) -> str:
        """마커가 아니었던 보류분은 돌려준다."""
        return _REC_RE.sub("", self.held)


def _stream_round(client: groq.Groq, api_messages: list[dict]) -> Iterator[dict]:
    """한 번의 모델 호출을 스트리밍. token 이벤트를 내보내고, 마지막에 {'type':'round_end', text, tool_calls}."""
    stream = client.chat.completions.create(
        model=MODEL, messages=api_messages, tools=TOOLS, tool_choice="auto",
        temperature=0.4, max_tokens=2048, stream=True, **_EXTRA,
    )
    text_parts: list[str] = []
    calls: dict[int, dict] = {}
    flt = _MarkerFilter()
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text_parts.append(delta.content)
            out = flt.feed(delta.content)
            if out:
                yield {"type": "token", "text": out}
        for tc in delta.tool_calls or []:
            slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function:
                slot["name"] += tc.function.name or ""
                slot["arguments"] += tc.function.arguments or ""
    tail = flt.flush()
    if tail:
        yield {"type": "token", "text": tail}
    yield {"type": "round_end", "text": "".join(text_parts), "tool_calls": [calls[i] for i in sorted(calls)]}


def _recommended_ids(text: str, seen_ids: set[int]) -> list[int]:
    """모델이 적은 id 중 이번 상담에서 도구로 실제 조회된 것만 통과시킨다."""
    ids: list[int] = []
    for m in _REC_RE.finditer(text):
        for tok in m.group(1).split(","):
            tok = tok.strip()
            if tok.isdigit() and int(tok) in seen_ids and int(tok) not in ids:
                ids.append(int(tok))
    return ids


def _system_message(profile: dict) -> dict:
    content = SYSTEM_PROMPT
    if profile:
        content += "\n[Known profile]\n" + json.dumps(profile, ensure_ascii=False)
    return {"role": "system", "content": content}


def chat_turn_stream(client: groq.Groq, history: list[dict], profile: dict, seen_ids: set[int]) -> Iterator[dict]:
    """history 끝에 user 메시지가 있는 상태로 호출. 어시스턴트/도구 턴을 history에 이어 붙이고
    token / products / profile 이벤트를 차례로 내보낸다."""
    retries = 0
    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        try:
            end = None
            for ev in _stream_round(client, [_system_message(profile)] + history):
                if ev["type"] == "round_end":
                    end = ev
                else:
                    yield ev
        except groq.BadRequestError:
            # 모델이 도구 호출을 잘못된 형식으로 만들었을 때(tool_use_failed): 한 번 더 시도
            retries += 1
            if retries > MAX_TOOL_RETRIES:
                yield {"type": "token", "text": "죄송해요, 답변을 만드는 데 문제가 생겼어요. 질문을 조금 바꿔서 다시 말씀해 주시겠어요?"}
                return
            continue

        rounds += 1
        text, calls = end["text"], end["tool_calls"]
        assistant: dict = {"role": "assistant", "content": text or None}
        if calls:
            assistant["tool_calls"] = [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for c in calls
            ]
        history.append(assistant)

        if not calls:
            ids = _recommended_ids(text, seen_ids)
            if ids:
                yield {"type": "products", "ids": ids}
            yield {"type": "profile", "profile": profile}
            return

        for c in calls:
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            history.append({
                "role": "tool", "tool_call_id": c["id"], "name": c["name"],
                "content": _run_tool(c["name"], args, profile, seen_ids),
            })

    yield {"type": "token", "text": "조건을 찾는 데 시간이 오래 걸리고 있어요. 한 번 더 말씀해 주시겠어요?"}
