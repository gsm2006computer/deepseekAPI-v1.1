import io
import json
import os
from typing import Any, Dict, List

import httpx
from docx import Document
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pypdf import PdfReader

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "这里直连替换成自己的API")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

MAX_EXTRACTED_CHARS = 30000
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".html", ".css", ".c", ".cpp", ".h", ".java", ".sql", ".log"
}

app = FastAPI(title="DeepSeek Local Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = "deepseek-chat"
    temperature: float = 0.7
    enable_search: bool = True


def extract_txt_content(raw_bytes: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def extract_docx_content(raw_bytes: bytes) -> str:
    doc = Document(io.BytesIO(raw_bytes))
    parts: List[str] = []

    for p in doc.paragraphs:
        txt = p.text.strip()
        if txt:
            parts.append(txt)

    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))

    return "\n".join(parts)


def extract_pdf_content(raw_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw_bytes))
    pages_text: List[str] = []

    for idx, page in enumerate(reader.pages, start=1):
        txt = (page.extract_text() or "").strip()
        if txt:
            pages_text.append(f"[第 {idx} 页]\n{txt}")

    return "\n\n".join(pages_text)


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)) -> Dict[str, Any]:
    filename = file.filename or "未命名文件"
    ext = os.path.splitext(filename)[1].lower()
    raw_bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    try:
        if ext in TEXT_EXTENSIONS:
            text = extract_txt_content(raw_bytes)
        elif ext == ".docx":
            text = extract_docx_content(raw_bytes)
        elif ext == ".pdf":
            text = extract_pdf_content(raw_bytes)
        elif ext == ".doc":
            raise HTTPException(
                status_code=400,
                detail="暂不支持旧版 .doc 二进制格式，请另存为 .docx 或 .pdf 后再上传"
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"暂不支持 {ext or '未知'} 格式，目前支持 .txt、.docx、.pdf 及常见文本/代码文件"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件解析失败：{str(e)}")

    text = text.strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail="未能从文件中提取到文本（可能是纯扫描图片型 PDF 或空白文档）"
        )

    truncated = False
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS]
        truncated = True

    return {
        "filename": filename,
        "ext": ext,
        "char_count": len(text),
        "truncated": truncated,
        "content": text,
    }


@app.post("/chat")
async def chat_stream(req: ChatRequest):
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 DEEPSEEK_API_KEY")

    msg_list: List[Dict[str, str]] = [
        {"role": m.role, "content": m.content} for m in req.messages
    ]

    # 如果勾选了联网搜索，给模型补充当前时间与联网说明提示
    if req.enable_search:
        msg_list.insert(
            0,
            {
                "role": "system",
                "content": "当前日期为 2026年11月20日。若问题涉及最新资讯、实时数据或外部信息，请结合最新事实作答，不确定之处明确说明。"
            },
        )

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": msg_list,
        "temperature": req.temperature,
        "stream": True,
    }

    async def event_generator():
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", DEEPSEEK_API_URL, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        err_raw = await resp.aread()
                        err_msg = err_raw.decode("utf-8", errors="replace")
                        err_payload = {"error": f"API 错误 {resp.status_code}: {err_msg}"}
                        yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
                        return

                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            raw = line[6:].strip()
                            if raw == "[DONE]":
                                yield "data: [DONE]\n\n"
                                break
                            try:
                                chunk_obj = json.loads(raw)
                                delta = (chunk_obj.get("choices") or [{}])[0].get("delta") or {}
                                content = delta.get("content")
                                if content:
                                    yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)