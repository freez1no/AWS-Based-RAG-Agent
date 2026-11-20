#!/usr/bin/env python3
"""웹 챗봇 서버. 실행:  python3 web/server.py   ->  http://127.0.0.1:8000

상위 폴더의 chatbot.py(KB 검색, 프롬프트, 설정)를 재사용하고, 답변을 스트리밍으로 브라우저에 전달합니다.
AWS 자격증명은 서버(.env)에만 있고 브라우저로 전달되지 않습니다.
"""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import chatbot as core  # noqa: E402  (.env 로드 포함)

from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
STATIC = HERE / "static"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
MAX_Q = 1000

if not core.KB_ID:
    sys.exit("KB_ID가 설정되지 않았습니다 (.env 확인).")
agent_rt, bedrock_rt = core.make_clients()


def clean_history(raw):
    out = []
    if isinstance(raw, list):
        for m in raw[-8:]:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str) and m["text"].strip():
                out.append({"role": m["role"], "content": [{"text": m["text"][:4000]}]})
    # Converse API는 첫 메시지가 user여야 함
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "TransitChat/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        f = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file() or f.suffix not in TYPES:
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        self._send(200, f.read_bytes(), TYPES[f.suffix], {
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'"})

    def _emit(self, obj):
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        if self.path != "/api/chat":
            return self._send(404, b"Not found", "text/plain")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, b"JSON only", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(min(n, 100_000)))
            q = str(data.get("question", "")).strip()
        except (ValueError, TypeError):
            return self._send(400, b"Bad request", "text/plain")
        if not q or len(q) > MAX_Q:
            return self._send(400, b"Invalid question", "text/plain")
        history = clean_history(data.get("history"))

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            chunks = core.retrieve(agent_rt, q)
            items = []
            for i, c in enumerate(chunks, 1):
                m = re.match(r"^(.*) \(p\.(\d+)\)$", core.source_name(c))
                name, page = (m.group(1), int(m.group(2))) if m else (core.source_name(c), None)
                items.append({"n": i, "name": name, "page": page, "score": round(c.get("score", 0), 2)})
            self._emit({"type": "sources", "items": items})
            if not chunks:
                self._emit({"type": "delta", "text": "안내서에서 관련 내용을 찾을 수 없습니다. 질문을 바꿔 다시 시도해 주세요."})
                return self._emit({"type": "done"})
            context = "\n\n".join(f"[{i}] (출처: {core.source_name(c)})\n{c['content']['text']}" for i, c in enumerate(chunks, 1))
            messages = history + [{"role": "user", "content": [{"text": f"[문서 발췌]\n{context}\n\n[질문]\n{q}"}]}]
            resp = bedrock_rt.converse_stream(
                modelId=core.MODEL_ID,
                system=[{"text": core.SYSTEM_PROMPT + "\n6. 마크다운은 굵게(**), 번호/글머리 목록만 사용하세요. 표와 코드블록은 쓰지 마세요."}],
                messages=messages,
                inferenceConfig={"maxTokens": 1024, "temperature": 0.1})
            for ev in resp["stream"]:
                t = ev.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if t:
                    self._emit({"type": "delta", "text": t})
            self._emit({"type": "done"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (BotoCoreError, ClientError) as e:
            try:
                self._emit({"type": "error", "message": core.explain_error(e)})
            except OSError:
                pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"웹 챗봇 실행 중: http://{HOST}:{PORT}  (종료: Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
