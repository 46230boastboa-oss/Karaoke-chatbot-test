import json          # faq.json, contacts.json 같은 파일을 읽고 쓰기 위한 파이썬 기본 도구
import os             # 환경변수(OPENAI_API_KEY 등)를 읽기 위한 파이썬 기본 도구
import csv            # 로그를 CSV 파일 형식으로 만들기 위한 파이썬 기본 도구
import io             # 파일을 디스크에 저장하지 않고 메모리 위에서 만들기 위한 도구 (CSV 다운로드용)
from datetime import datetime          # 로그에 '언제 질문했는지' 시각을 기록하기 위함
from collections import Counter        # 미답변 질문 중 '어떤 질문이 몇 번 나왔는지' 세기 위함

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel          # 데이터의 모양(타입)을 정의해 실수로 잘못된 값이 들어오는 것을 막아주는 도구
from openai import OpenAI               # GPT-4o mini를 호출하기 위한 OpenAI 공식 라이브러리


# ============================================================================
# 1. 기본 설정
# ============================================================================

# FastAPI 앱을 하나 만듭니다. 이 app이라는 이름이 Render.com의
# Start Command(uvicorn main:app ...)에서 그대로 쓰입니다.
app = FastAPI(title="지방세무 카카오톡 FAQ 챗봇")

# 파일 경로들을 한 곳에 모아둡니다. 나중에 파일 이름이 바뀌어도 여기만 고치면 됩니다.
FAQ_FILE = "faq.json"
CONTACTS_FILE = "contacts.json"
UNANSWERED_LOG_FILE = "logs/unanswered.csv"

# OpenAI API 키는 코드에 직접 적지 않고, 환경변수에서 읽어옵니다.
# 로컬 테스트 시에는 .env 파일에 적어둔 값을, Render 배포 후에는
# Render 대시보드의 Environment Variables에 등록한 값을 자동으로 읽어옵니다.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# FAQ 추가/수정/삭제, 로그 다운로드 같은 '관리자 전용' 기능을 보호하기 위한 비밀번호입니다.
# 이 값도 반드시 환경변수로 관리하고, 코드에 직접 적지 마세요.
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "change-me-please")

# 사용할 GPT 모델 이름. 비용 절감을 위해 반드시 mini 모델을 사용합니다.
INTENT_MODEL = "gpt-4o-mini"


# ============================================================================
# 2. 데이터 모양 정의 (Pydantic 모델)
# ----------------------------------------------------------------------------
# 아래는 "FAQ 하나는 이런 항목들로 이루어져 있어야 한다"는 설계도입니다.
# FastAPI가 이 설계도를 보고, 형식에 안 맞는 요청이 들어오면 자동으로 에러를
# 돌려줘서 잘못된 데이터가 저장되는 것을 막아줍니다.
# ============================================================================

class FaqItem(BaseModel):
    id: str            # FAQ 고유 번호 (예: F001)
    category: str      # 세목 카테고리 (예: 재산세-토지)
    question: str       # 예시 질문
    answer: str         # 실제로 사용자에게 보여줄 답변 (원문 그대로 출력됨)
    keywords: list[str] = []   # 이 질문과 관련된 핵심 단어들 (매칭 정확도를 높이기 위함)


class FaqCreate(BaseModel):
    # 새 FAQ를 추가할 때 받는 데이터. id는 서버가 자동으로 만들어주므로 받지 않습니다.
    category: str
    question: str
    answer: str
    keywords: list[str] = []


class FaqUpdate(BaseModel):
    # 기존 FAQ를 수정할 때 받는 데이터. 값을 입력한 항목만 바뀌도록 전부 선택 항목(Optional)입니다.
    category: str | None = None
    question: str | None = None
    answer: str | None = None
    keywords: list[str] | None = None


# ============================================================================
# 3. 파일 읽기/쓰기 도우미 함수
# ----------------------------------------------------------------------------
# faq.json, contacts.json을 매번 똑같은 방식으로 열고 닫는 코드가 반복되지 않도록
# 함수 하나로 묶어둔 것입니다.
# ============================================================================

def load_json(path: str):
    """지정한 경로의 JSON 파일을 읽어서 파이썬 데이터(리스트/딕셔너리)로 돌려줍니다."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data):
    """파이썬 데이터를 JSON 파일로 저장합니다. ensure_ascii=False로 해야 한글이
    깨지지 않고 그대로 저장됩니다."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_faq() -> list[dict]:
    return load_json(FAQ_FILE)


def save_faq(faq_list: list[dict]):
    save_json(FAQ_FILE, faq_list)


def load_contacts() -> dict:
    return load_json(CONTACTS_FILE)


def check_admin(x_admin_key: str | None):
    """FAQ 수정이나 로그 다운로드 같은 민감한 기능을 아무나 못 쓰도록 지키는
    '문지기' 함수입니다. 요청 헤더에 X-Admin-Key라는 항목으로 비밀번호를 같이
    보내야만 통과시킵니다. 비밀번호가 틀리면 403(권한 없음) 에러를 돌려줍니다."""
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="관리자 인증에 실패했습니다.")


def log_unanswered(question: str, guessed_category: str):
    """AI가 답을 찾지 못한 질문을 CSV 파일에 한 줄씩 계속 추가(append)로 쌓아둡니다.
    나중에 이 기록을 보고 FAQ에 어떤 질문을 추가해야 할지 판단할 수 있습니다."""
    os.makedirs("logs", exist_ok=True)  # logs 폴더가 없으면 새로 만듦
    file_exists = os.path.exists(UNANSWERED_LOG_FILE)
    with open(UNANSWERED_LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            # 파일을 처음 만드는 경우에만 맨 위에 제목 줄(header)을 적습니다.
            writer.writerow(["timestamp", "question", "guessed_category"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"), question, guessed_category])


# ============================================================================
# 4. 1단계 매칭: 키워드로 먼저 빠르게 찾아보기
# ----------------------------------------------------------------------------
# 모든 질문마다 GPT를 호출하면 비용과 응답 시간이 늘어납니다. 그래서 먼저
# faq.json에 등록해둔 keywords와 사용자의 질문을 단순 비교해서, 확실하게
# 겹치는 FAQ가 있으면 AI 호출 없이 바로 답을 찾아냅니다. 애매한 경우에만
# 아래 5단계의 GPT-4o mini에게 판단을 맡깁니다.
# ============================================================================

def keyword_match(user_text: str, faq_list: list[dict]) -> dict | None:
    """사용자 질문 안에 FAQ의 keywords가 2개 이상 포함되어 있으면 '확실한 매칭'으로
    보고 그 FAQ를 바로 돌려줍니다. 그렇지 않으면 None(못 찾음)을 돌려줍니다."""
    best_match = None
    best_score = 0
    for faq in faq_list:
        score = sum(1 for kw in faq["keywords"] if kw in user_text)
        if score > best_score:
            best_score = score
            best_match = faq
    if best_score >= 2:
        return best_match
    return None


# ============================================================================
# 5. 2단계 매칭: GPT-4o mini에게 '의도 분류'만 맡기기
# ----------------------------------------------------------------------------
# 중요: 아래 함수는 GPT에게 "답을 만들어줘"라고 절대 요청하지 않습니다.
# "이 질문이 FAQ 목록 중 몇 번과 같은 뜻이야?"라고만 물어보고,
# 실제 답변 문장은 이 함수 밖에서 faq.json 원문을 그대로 가져와 사용합니다.
# ============================================================================

def classify_intent_with_ai(user_text: str, faq_list: list[dict]) -> dict:
    """GPT-4o mini를 호출해서 사용자 질문의 의도를 분류합니다.
    반환값 예시: {"matched_id": "F003", "category": "자동차세"}
    매칭되는 FAQ가 없다고 판단되면 matched_id는 None이 됩니다."""

    # GPT에게 넘겨줄 FAQ 목록을 만듭니다. 답변(answer)은 절대 포함하지 않고,
    # id/category/question만 알려줘서, GPT가 답을 베끼거나 지어낼 여지를 없앱니다.
    faq_summary = [
        {"id": f["id"], "category": f["category"], "question": f["question"]}
        for f in faq_list
    ]

    system_prompt = (
        "너는 지방세 관련 카카오톡 챗봇의 '질문 분류기'다. "
        "사용자 질문을 보고, 아래 FAQ 목록 중 의미가 가장 비슷한 항목의 id를 골라라. "
        "절대로 답변 문장을 스스로 만들어내지 말고, 오직 분류만 해야 한다. "
        "비슷한 FAQ가 하나도 없다고 판단되면 matched_id를 null로 응답하라. "
        "반드시 다음 JSON 형식으로만 답하라: "
        '{"matched_id": "F001 또는 null", "category": "재산세-토지 등 카테고리 이름 또는 기타"}'
    )

    user_prompt = (
        f"[FAQ 목록]\n{json.dumps(faq_summary, ensure_ascii=False)}\n\n"
        f"[사용자 질문]\n{user_text}"
    )

    response = client.chat.completions.create(
        model=INTENT_MODEL,
        response_format={"type": "json_object"},  # GPT가 JSON 형식으로만 답하도록 강제
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,  # 0으로 두면 매번 같은 질문에 최대한 일관된 답을 하도록 랜덤성을 줄여줍니다.
    )

    result_text = response.choices[0].message.content
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        # 혹시라도 GPT가 형식을 어긴 JSON을 돌려주면, 안전하게 '매칭 실패'로 처리합니다.
        result = {"matched_id": None, "category": "기타"}
    return result


# ============================================================================
# 6. 카카오톡에게 돌려줄 응답 모양 만들기
# ----------------------------------------------------------------------------
# 카카오 i 오픈빌더는 정해진 JSON 형식으로 응답해야만 화면에 말풍선을 띄워줍니다.
# 매번 이 형식을 직접 타이핑하지 않도록 함수로 만들어둡니다.
# ============================================================================

def kakao_simple_text(text: str) -> dict:
    """카카오톡 말풍선 하나에 텍스트를 담아 돌려주는 표준 응답 형식입니다."""
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {"simpleText": {"text": text}}
            ]
        },
    }


# ============================================================================
# 7. API 엔드포인트(실제 기능들)
# ============================================================================

@app.get("/")
def health_check():
    """서버가 살아있는지 확인하는 가장 간단한 주소입니다.
    브라우저에서 배포된 주소로 접속했을 때 이 메시지가 보이면 정상 작동 중인 것입니다."""
    return {"status": "ok", "message": "지방세무 챗봇 서버가 정상 작동 중입니다."}


@app.post("/kakao/webhook")
async def kakao_webhook(request: Request):
    """카카오 i 오픈빌더가 실제로 질문을 보내는 주소(웹훅)입니다.
    이 주소를 오픈빌더 '스킬' 설정 화면의 URL 칸에 등록하면 됩니다."""

    body = await request.json()

    # 카카오가 보내주는 요청 안에서, 사용자가 실제로 입력한 문장을 꺼냅니다.
    # (카카오 스킬 요청의 표준 구조: userRequest.utterance)
    user_text = body.get("userRequest", {}).get("utterance", "").strip()

    if not user_text:
        return JSONResponse(kakao_simple_text("질문 내용을 인식하지 못했어요. 다시 입력해 주세요."))

    faq_list = load_faq()

    # 1단계: 키워드로 먼저 빠르게 찾아본다 (비용 없음, 속도 빠름)
    matched = keyword_match(user_text, faq_list)

    # 2단계: 키워드로 못 찾았으면, GPT-4o mini에게 '분류'만 맡긴다
    guessed_category = "기타"
    if matched is None:
        intent_result = classify_intent_with_ai(user_text, faq_list)
        guessed_category = intent_result.get("category") or "기타"
        matched_id = intent_result.get("matched_id")
        if matched_id:
            matched = next((f for f in faq_list if f["id"] == matched_id), None)

    if matched:
        # 매칭에 성공한 경우: faq.json에 미리 적어둔 답변 '원문 그대로'를 돌려줍니다.
        # (절대 AI가 이 문장을 새로 만들지 않습니다.)
        return JSONResponse(kakao_simple_text(matched["answer"]))

    # 여기까지 왔다면 매칭 실패 -> 담당자 연락처 안내 + 로그 기록
    contacts = load_contacts()
    contact = contacts.get(guessed_category, contacts.get("기타"))
    log_unanswered(user_text, guessed_category)

    answer = (
        "죄송해요, 정확한 답변을 찾지 못했어요.\n"
        f"담당 부서인 '{contact['department']}'({contact['phone']})로 문의해 주시면 "
        "자세히 안내받으실 수 있습니다."
    )
    return JSONResponse(kakao_simple_text(answer))


# ---------------------------------------------------------------------------
# 7-1. FAQ 조회 (배포 후에도 코드를 건드리지 않고 확인/관리하기 위한 기능)
# ---------------------------------------------------------------------------

@app.get("/faq")
def get_all_faq():
    """등록된 모든 FAQ 목록을 보여줍니다. 관리 화면이나 엑셀 업로드 전 확인용으로 씁니다."""
    return load_faq()


@app.post("/faq")
def add_faq(item: FaqCreate, x_admin_key: str | None = Header(default=None)):
    """새로운 FAQ를 하나 추가합니다. 반드시 요청 헤더에 X-Admin-Key(관리자 비밀번호)를
    함께 보내야 합니다. (아무나 FAQ를 조작하지 못하도록 보호하는 장치)"""
    check_admin(x_admin_key)

    faq_list = load_faq()
    # 새 id는 기존 개수를 기준으로 F008, F009 ... 순서로 자동 생성합니다.
    new_id = f"F{len(faq_list) + 1:03d}"
    new_item = {"id": new_id, **item.dict()}
    faq_list.append(new_item)
    save_faq(faq_list)
    return {"message": "FAQ가 추가되었습니다.", "item": new_item}


@app.put("/faq/{faq_id}")
def update_faq(faq_id: str, item: FaqUpdate, x_admin_key: str | None = Header(default=None)):
    """기존 FAQ의 내용을 수정합니다. 값을 보낸 항목만 바뀌고, 나머지는 그대로 유지됩니다."""
    check_admin(x_admin_key)

    faq_list = load_faq()
    target = next((f for f in faq_list if f["id"] == faq_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"{faq_id}를 찾을 수 없습니다.")

    # exclude_unset=True: 사용자가 실제로 입력한 항목만 골라내서 덮어씁니다.
    update_data = item.dict(exclude_unset=True)
    target.update(update_data)
    save_faq(faq_list)
    return {"message": "FAQ가 수정되었습니다.", "item": target}


@app.delete("/faq/{faq_id}")
def delete_faq(faq_id: str, x_admin_key: str | None = Header(default=None)):
    """FAQ 하나를 삭제합니다."""
    check_admin(x_admin_key)

    faq_list = load_faq()
    new_list = [f for f in faq_list if f["id"] != faq_id]
    if len(new_list) == len(faq_list):
        raise HTTPException(status_code=404, detail=f"{faq_id}를 찾을 수 없습니다.")
    save_faq(new_list)
    return {"message": f"{faq_id}가 삭제되었습니다."}


# ---------------------------------------------------------------------------
# 7-2. 미답변 질문 로그 확인 / CSV 다운로드 / 빈도 요약
# ---------------------------------------------------------------------------

@app.get("/logs/unanswered")
def get_unanswered_logs(x_admin_key: str | None = Header(default=None)):
    """AI가 답하지 못했던 질문들을 화면(JSON)으로 확인합니다."""
    check_admin(x_admin_key)

    if not os.path.exists(UNANSWERED_LOG_FILE):
        return []

    with open(UNANSWERED_LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


@app.get("/logs/unanswered/csv")
def download_unanswered_csv(x_admin_key: str | None = Header(default=None)):
    """미답변 질문 로그 전체를 CSV 파일로 한 번에 다운로드합니다.
    엑셀에서 바로 열어 FAQ 추가 여부를 검토할 때 사용합니다."""
    check_admin(x_admin_key)

    if not os.path.exists(UNANSWERED_LOG_FILE):
        raise HTTPException(status_code=404, detail="아직 기록된 미답변 로그가 없습니다.")

    with open(UNANSWERED_LOG_FILE, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # 브라우저가 이 응답을 '파일 다운로드'로 처리하도록 헤더를 지정합니다.
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=unanswered.csv"},
    )


@app.get("/logs/unanswered/summary")
def summarize_unanswered(x_admin_key: str | None = Header(default=None)):
    """미답변 질문을 '많이 나온 순서'로 정리해서 보여줍니다.
    어떤 FAQ를 우선적으로 추가해야 할지 판단할 때 유용합니다."""
    check_admin(x_admin_key)

    if not os.path.exists(UNANSWERED_LOG_FILE):
        return []

    with open(UNANSWERED_LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        questions = [row["question"] for row in reader]

    counter = Counter(questions)
    # most_common()은 (질문, 등장횟수)를 등장 횟수가 많은 순서로 정렬해서 돌려줍니다.
    ranked = [{"question": q, "count": c} for q, c in counter.most_common()]
    return ranked
