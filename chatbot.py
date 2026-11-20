#!/usr/bin/env python3
"""교통약자 이용 안내 챗봇 (AWS Bedrock Knowledge Base + Bedrock LLM, CLI).

실행:  python3 chatbot.py
설정:  같은 폴더의 .env 파일 (또는 환경변수). .env.example 참고.
흐름:  질문 -> KB 검색(bedrock-agent-runtime.retrieve) -> 근거 기반 답변(bedrock-runtime.converse) -> 출처 출력
"""
import os
import sys
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3가 필요합니다:  pip3 install boto3")


def load_env(path):
    """간단한 .env 로더 (이미 설정된 환경변수는 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(Path(__file__).resolve().parent / ".env")

REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2"
KB_ID = os.getenv("KB_ID", "")
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
TOP_K = int(os.getenv("TOP_K", "5"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.3"))

SYSTEM_PROMPT = (
    "당신은 교통약자를 위한 대중교통 이용 안내 도우미입니다. "
    "저상버스, 휠체어 환승 경로, 엘리베이터, 편의시설 등에 대한 질문에 답합니다.\n"
    "규칙:\n"
    "1. 반드시 제공된 [문서 발췌]에 있는 내용만 근거로 답하세요. 추측하거나 외부 지식을 섞지 마세요.\n"
    "2. [휠체어 환승·엘리베이터 안내] 환승 경로와 엘리베이터 정보는 문서에 근거해 정확하게 안내하고, "
    "순서가 있는 이동 방법은 번호 목록(3~5단계 이내)으로 쉽게 설명하세요. "
    "정류장 방향이나 정류소번호가 불명확하면 먼저 짧게 되물어 확인하고, "
    "확인되지 않은 구간을 '휠체어 통행 가능' 또는 '무장애 환승 보장'으로 단정하지 마세요.\n"
    "3. [저상버스·근거 출처] 저상버스 운행 정보는 확인된 공식 내용, 과거 조사 이력, 실시간 확인 필요 사항을 구분해 답하세요 "
    "(과거 조사 대수를 현재 운행 대수로 단정하거나 모든 차량이 저상버스라고 단정 금지). "
    "각 핵심 문장 끝에 근거가 된 발췌 번호를 [1], [2] 형식으로 표시하고, 문서에 나온 출처명 또는 출처 ID도 함께 제시하세요.\n"
    "4. [미확인 정보·문의처 안내] 문서에 없는 정보(출구별 상세 위치, 실시간 고장·도착 시간, 정류장 보도 턱·경사로 유무 등)는 "
    "임의로 생성하지 말고 '안내서에서 해당 내용을 찾을 수 없습니다.'라고 말한 뒤, "
    "질문에 맞는 공식 문의처나 확인 방법을 안내하세요 "
    "(승강기 위치·고장·역 내 이동: 부산교통공사 고객센터 1544-5005 / "
    "버스 노선·실시간 도착·저상버스 여부: 부산버스정보시스템 / "
    "저상버스 정책·도입 현황: 부산광역시 대중교통과 051-888-3968).\n"
    "5. [쉬운 말·짧은 답변] 전문용어를 최소화하고 교통약자가 이해하기 쉬운 말을 사용하세요 "
    "(예: '무장애 동선' → '계단 없이 갈 수 있는 길'). "
    "결론부터 먼저 말하고, 확인된 사실과 추가 확인 사항을 구분해 2~3문장(약 100자 내외, 경로 설명은 짧은 번호 목록)으로 간결하게 답하세요.\n"
    "6. 사용자의 질문이 애매할 때(환승정보를 요청했으나 버스 번호 또는 정류장 이름을 언급하지 않는 경우)는, 필요 정보(버스 번호, 정류장 이름 등)를 포함하여 다시 질문 해 달라고 답변하세요."
    "7. [답변 일관성] 동일한 의미의 질문을 다른 표현으로 묻더라도 핵심 사실, 수치, 이용 절차, 문의처, 근거 출처가 "
    "항상 일관되게 유지되도록 답하세요.\n"
    "8. 질문과 같은 언어(기본 한국어)로 답하세요."
)


def make_clients():
    cfg = Config(region_name=REGION, retries={"max_attempts": 3, "mode": "standard"})
    session = boto3.Session(region_name=REGION)  # .env / 환경변수 / ~/.aws 자격증명 순으로 자동 탐색
    if session.get_credentials() is None:
        raise NoCredentialsError()
    return (
        session.client("bedrock-agent-runtime", config=cfg),
        session.client("bedrock-runtime", config=cfg),
    )


def source_name(result):
    loc = result.get("location", {})
    uri = loc.get("s3Location", {}).get("uri") or loc.get("webLocation", {}).get("url") or "알 수 없음"
    meta = result.get("metadata", {})
    page = meta.get("x-amz-bedrock-kb-document-page-number")
    name = uri.rsplit("/", 1)[-1]
    return name + (f" (p.{int(page)})" if page is not None else "")


def retrieve(agent_rt, question):
    resp = agent_rt.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": TOP_K}},
    )
    results = resp.get("retrievalResults", [])
    return [r for r in results if r.get("score", 1.0) >= MIN_SCORE]


def answer(bedrock_rt, question, history, chunks):
    context = "\n\n".join(
        f"[{i}] (출처: {source_name(c)})\n{c['content']['text']}" for i, c in enumerate(chunks, 1)
    )
    user_text = f"[문서 발췌]\n{context}\n\n[질문]\n{question}"
    messages = history + [{"role": "user", "content": [{"text": user_text}]}]
    resp = bedrock_rt.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={"maxTokens": 1024, "temperature": 0.1},
    )
    return resp["output"]["message"]["content"][0]["text"]


def explain_error(e):
    if isinstance(e, NoCredentialsError):
        return "AWS 자격증명을 찾을 수 없습니다. .env에 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY를 설정하세요."
    if isinstance(e, ClientError):
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        hints = {
            "ResourceNotFoundException": "KB_ID 또는 리전이 올바른지 확인하세요.",
            "AccessDeniedException": "IAM 권한(bedrock:Retrieve, bedrock:InvokeModel)과 모델 액세스 허용 여부를 확인하세요.",
            "ValidationException": "MODEL_ID가 해당 리전에서 유효한지 확인하세요.",
            "UnrecognizedClientException": "Access Key가 올바르지 않습니다.",
            "ExpiredTokenException": "자격증명(세션 토큰)이 만료되었습니다.",
        }
        return f"{code}: {msg}\n  -> {hints.get(code, '')}"
    return str(e)


def main():
    if not KB_ID:
        sys.exit("KB_ID가 설정되지 않았습니다. .env 파일에 KB_ID=<지식기반 ID>를 추가하세요.")
    try:
        agent_rt, bedrock_rt = make_clients()
    except Exception as e:
        sys.exit(explain_error(e))

    print("=" * 60)
    print(" 교통약자 이용 안내 챗봇  (AWS Bedrock Knowledge Base)")
    print(f" region={REGION}  KB={KB_ID}")
    print(" 종료: exit / quit / q    대화 초기화: /reset")
    print("=" * 60)

    history = []  # 이전 대화(검색 발췌 제외한 질문/답변)만 유지
    while True:
        try:
            q = input("\n질문> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "q", "종료"):
            print("종료합니다.")
            break
        if q == "/reset":
            history.clear()
            print("대화를 초기화했습니다.")
            continue

        try:
            chunks = retrieve(agent_rt, q)
            if not chunks:
                print("\n안내서에서 관련 내용을 찾을 수 없습니다. 질문을 바꿔 다시 시도해 주세요.")
                continue
            text = answer(bedrock_rt, q, history, chunks)
        except (BotoCoreError, ClientError) as e:
            print(f"\n[오류] {explain_error(e)}")
            continue

        history += [
            {"role": "user", "content": [{"text": q}]},
            {"role": "assistant", "content": [{"text": text}]},
        ]
        history = history[-8:]

        print(f"\n답변:\n{text}\n\n근거 문서:")
        for i, c in enumerate(chunks, 1):
            snippet = " ".join(c["content"]["text"].split())[:120]
            print(f"  [{i}] {source_name(c)}  (유사도 {c.get('score', 0):.2f})\n      \"{snippet}...\"")


if __name__ == "__main__":
    main()
