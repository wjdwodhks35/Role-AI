"""Run bounded PDF parsing in a disposable Linux/WSL subprocess."""
import io
import json
import subprocess
import sys

from fastapi import HTTPException


def extract_pdf(data):
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "PDF 형식이 아닙니다.")
    if not sys.platform.startswith("linux"):
        raise HTTPException(503, "PDF 추출은 메모리 제한이 가능한 WSL2/Linux에서 지원합니다.")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "role_ai.pdf_extract"],
            input=data, capture_output=True, timeout=12, check=False,
        )
        if result.returncode:
            raise ValueError("parser failed")
        payload = json.loads(result.stdout)
        if not payload.get("ok"):
            raise HTTPException(422, payload.get("reason", "PDF를 읽을 수 없습니다."))
        return payload["text"]
    except HTTPException:
        raise
    except (subprocess.TimeoutExpired, ValueError, OSError):
        raise HTTPException(422, "PDF 처리 제한을 초과했거나 문서가 손상되었습니다.") from None


def worker():
    import resource
    # Limits apply before importing the PDF parser or decompressing content.
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(524289)), strict=True)
        if reader.is_encrypted:
            raise ValueError("암호화 PDF는 지원하지 않습니다.")
        if not 1 <= len(reader.pages) <= 20:
            raise ValueError("PDF는 최대 20페이지입니다.")
        parts = []
        total = 0
        for index, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 2 * 1024 * 1024:
                raise ValueError("PDF 페이지 처리 크기를 초과했습니다.")
            content = (page.extract_text() or "").strip()
            if content:
                part = f"[페이지 {index}]\n{content}"
                total += len(part) + 2
                if total > 16000:
                    raise ValueError("추출 텍스트는 최대 16,000자입니다.")
                parts.append(part)
        if not parts:
            raise ValueError("텍스트가 없습니다. 스캔·이미지 PDF에는 OCR이 필요합니다.")
        print(json.dumps({"ok": True, "text": "\n\n".join(parts)}, ensure_ascii=True))
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=True))
    except Exception:
        print(json.dumps({"ok": False, "reason": "PDF가 손상되었거나 추출할 수 없습니다."}))


if __name__ == "__main__":
    worker()
