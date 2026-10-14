import io
import json

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from role_ai.providers import ModelReply


def pdf_bytes(text="deployment handbook", pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
            page[NameObject("/MediaBox")] = ArrayObject(page["/MediaBox"])
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload_pdf(client, data=None, filename="guide.pdf", project=""):
    return client.post("/api/documents/pdf", params={"filename": filename, "project": project},
                       content=data if data is not None else pdf_bytes(),
                       headers={"Content-Type": "application/pdf"})


def document(client, text="car maintenance guide", project=""):
    return client.post("/api/documents", json={
        "filename": "guide.txt", "content": text, "project": project}).json()["id"]


def configure(client, response=None, metadata=None):
    client.app.state.settings.embedding_model = "test-embed:latest"
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path == "/api/show":
            return httpx.Response(200, json=metadata or {"capabilities": ["embedding"]})
        vectors = [[1.0, 0.0] if ("automobile" in text or "car" in text)
                   else [0.0, 1.0] for text in body["input"]]
        return httpx.Response(200, json=response if response is not None else {
            "model": "test-embed:latest", "embeddings": vectors})
    client.app.state.semantic.transport = httpx.MockTransport(handler)
    return calls


def semantic(client, did, **extra):
    return client.post("/api/documents/semantic", json={
        "query": "automobile repair", "document_ids": [did], **extra})


def test_pdf_extract_store_search_export(signed_in):
    response = upload_pdf(signed_in)
    assert response.status_code == 201
    item = response.json()
    assert item["format"] == "extracted_pdf_text" and not item["original_retained"]
    did = item["id"]
    content = signed_in.get("/api/documents/" + did).json()["content"]
    assert "[페이지 1]" in content and "deployment handbook" in content
    assert signed_in.get("/api/documents/search?q=deployment").json()["sources"][0]["document_id"] == did
    export = signed_in.get("/api/documents/" + did + "/export")
    assert export.text == content and export.headers["content-type"].startswith("text/plain")
    assert upload_pdf(signed_in).json()["duplicate"]


@pytest.mark.parametrize("kind", ["invalid", "encrypted", "blank", "pages", "large_text", "path"])
def test_pdf_rejects_unsupported_and_malformed(signed_in, kind):
    data = {"invalid": b"%PDF-broken", "encrypted": pdf_bytes(password="secret"),
            "blank": pdf_bytes(text=""), "pages": pdf_bytes(pages=21),
            "large_text": pdf_bytes(text="a" * 16001)}.get(kind, pdf_bytes())
    response = upload_pdf(signed_in, data, filename="../bad.pdf" if kind == "path" else "guide.pdf")
    assert response.status_code == 422
    assert signed_in.get("/api/documents").json() == []
    if kind == "blank":
        assert "OCR" in response.json()["detail"]


def test_pdf_body_limit_and_csrf(signed_in):
    assert upload_pdf(signed_in, b"%PDF-" + b"x" * 524288).status_code == 413
    assert upload_pdf(signed_in, pdf_bytes(text="a" * 70000)).status_code == 422
    signed_in.headers.pop("X-CSRF-Token")
    assert upload_pdf(signed_in).status_code == 403
    assert signed_in.get("/api/documents").json() == []


def test_pdf_parser_timeout_and_platform(signed_in, monkeypatch):
    import subprocess
    import role_ai.pdf_extract as parser
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("pdf", 12)
    monkeypatch.setattr(parser.subprocess, "run", timeout)
    assert upload_pdf(signed_in).status_code == 422
    monkeypatch.setattr(parser.sys, "platform", "win32")
    assert upload_pdf(signed_in).status_code == 503


def test_semantic_synonyms_without_keyword_match(signed_in):
    did = document(signed_in)
    other = document(signed_in, "cooking soup")
    calls = configure(signed_in)
    assert signed_in.get("/api/documents/search?q=automobile").json()["sources"] == []
    result = semantic(signed_in, did, document_ids=[did, other]).json()
    assert result["mode"] == "semantic"
    assert [s["document_id"] for s in result["sources"]] == [did]
    assert result["sources"][0]["score"] == 1.0
    assert calls[1][0] == "/api/embed"
    assert calls[1][1]["truncate"] is False and calls[1][1]["keep_alive"] == 0


def test_semantic_disabled_and_no_hidden_fallback(signed_in):
    did = document(signed_in)
    assert semantic(signed_in, did).status_code == 503
    assert signed_in.get("/api/documents/search?q=car").json()["sources"]
    calls = configure(signed_in, response={"model": "wrong", "embeddings": [[1, 0], [1, 0]]})
    assert semantic(signed_in, did).status_code == 503
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    {"model": "test-embed:latest", "embeddings": []},
    {"model": "test-embed:latest", "embeddings": [[1, 0], [1]]},
    {"model": "test-embed:latest", "embeddings": [[0, 0], [1, 0]]},
    {"model": "test-embed:latest", "embeddings": [[True, 0], [1, 0]]},
    {"model": "test-embed:latest", "embeddings": [[1e20, 0], [1, 0]]},
    {"model": "test-embed:latest", "embeddings": [[1, 0], ["x", 0]]},
    {"model": "test-embed:latest", "embeddings": "invalid"},
])
def test_semantic_invalid_vectors(signed_in, response):
    did = document(signed_in)
    configure(signed_in, response=response)
    assert semantic(signed_in, did).status_code == 503


def test_semantic_remote_and_cloud_models_blocked(signed_in):
    did = document(signed_in)
    calls = configure(signed_in, metadata={"capabilities": ["embedding"], "remote_host": "https://ollama.com"})
    assert semantic(signed_in, did).status_code == 503
    assert len(calls) == 1
    signed_in.app.state.settings.ollama_url = "https://remote.example"
    assert semantic(signed_in, did).status_code == 503
    assert len(calls) == 1


def test_semantic_user_project_and_csrf_before_inference(signed_in):
    did = document(signed_in, project="private")
    calls = configure(signed_in)
    assert semantic(signed_in, did).status_code == 404
    assert calls == []
    response = signed_in.post("/api/auth/register", json={
        "email": "other@example.test", "password": "safe-test-password-123"})
    signed_in.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert semantic(signed_in, did, project="private").status_code == 404
    assert calls == []
    signed_in.headers.pop("X-CSRF-Token")
    assert semantic(signed_in, did).status_code == 403


def test_semantic_chat_context_history_and_paid_block(signed_in):
    did = document(signed_in)
    configure(signed_in)
    captured = []
    class Model:
        async def complete(self, messages):
            captured.append(messages)
            return ModelReply("car handbook reference", "ollama", "test-local")
    signed_in.app.state.providers.providers["ollama"] = Model()
    cid = signed_in.post("/api/conversations", json={}).json()["id"]
    path = f"/api/conversations/{cid}/chat"
    body = {"content": "automobile repair", "provider": "ollama",
            "document_ids": [did], "retrieval_mode": "semantic"}
    result = signed_in.post(path, json=body)
    assert result.status_code == 200
    assert result.json()["retrieval"]["mode"] == "semantic"
    assert "car maintenance" in captured[0][0]["content"]
    assert signed_in.get(f"/api/conversations/{cid}/messages").json()[1]["sources"][0]["score"] == 1
    assert signed_in.post(path, json={**body, "provider": "openai", "allow_paid": True}).status_code == 403


def test_semantic_timeout_no_history_and_gpu_lock(signed_in):
    from role_ai.artifacts import storage_lock
    did = document(signed_in)
    configure(signed_in)
    def timeout(request):
        raise httpx.ReadTimeout("test timeout")
    signed_in.app.state.semantic.transport = httpx.MockTransport(timeout)
    cid = signed_in.post("/api/conversations", json={}).json()["id"]
    response = signed_in.post(f"/api/conversations/{cid}/chat", json={
        "content": "automobile", "provider": "ollama", "document_ids": [did],
        "retrieval_mode": "semantic"})
    assert response.status_code == 503
    assert signed_in.get(f"/api/conversations/{cid}/messages").json() == []
    configure(signed_in)
    with storage_lock(signed_in.app.state.settings.storage_dir):
        assert semantic(signed_in, did).status_code == 503
