"""Bounded, owner-filtered text retrieval. No paid API or remote embeddings."""
import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text

from role_ai.models import Document, DocumentChunk

MAX_DOCUMENTS = 32
MAX_TEXT = 16000


class DocumentInput(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=MAX_TEXT)
    project: str = Field(default="", max_length=80)

    @field_validator("filename")
    @classmethod
    def filename_safe(cls, value):
        if (value != value.strip() or "/" in value or "\\" in value
                or any(ord(c) < 32 for c in value)
                or not value.lower().endswith((".txt", ".md"))):
            raise ValueError("경로 없는 .txt 또는 .md 파일 이름이 필요합니다.")
        return value

    @field_validator("content")
    @classmethod
    def text_only(cls, value):
        if not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("비어 있지 않은 텍스트 문서가 필요합니다.")
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def document_dict(item):
    return {"id": item.id, "filename": item.filename, "project": item.project,
            "characters": len(item.content), "created_at": item.created_at}


class DocumentRepository:
    def __init__(self, db):
        self.db = db

    def get(self, uid, did):
        item = self.db.scalar(select(Document).where(
            Document.user_id == uid, Document.id == did))
        if not item:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        return item

    def create(self, uid, body):
        self.db.rollback()
        self.db.execute(text("BEGIN IMMEDIATE"))
        digest = hashlib.sha256(body.content.encode()).hexdigest()
        existing = self.db.scalar(select(Document).where(
            Document.user_id == uid, Document.project == body.project,
            Document.digest == digest))
        if existing:
            return existing, True
        count = self.db.scalar(select(func.count()).select_from(Document).where(
            Document.user_id == uid))
        if count >= MAX_DOCUMENTS:
            raise HTTPException(409, "문서는 계정당 최대 32개입니다. 불필요한 문서를 삭제하세요.")
        item = Document(user_id=uid, filename=body.filename, content=body.content,
                        project=body.project, digest=digest)
        self.db.add(item)
        self.db.flush()
        for position, offset in enumerate(range(0, len(body.content), 700), 1):
            self.db.add(DocumentChunk(document_id=item.id, position=position,
                                      content=body.content[offset:offset + 800]))
        self.db.flush()
        return item, False

    def list(self, uid):
        return list(self.db.scalars(select(Document).where(
            Document.user_id == uid).order_by(Document.created_at.desc())))

    def search(self, uid, query, project="", document_ids=None):
        if document_ids is not None:
            for did in set(document_ids):
                item = self.get(uid, did)
                if item.project not in {"", project}:
                    raise HTTPException(404, "현재 프로젝트에서 문서를 찾을 수 없습니다.")
            if not document_ids:
                return []
        words = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))[:20]
        if not words:
            return []
        statement = select(DocumentChunk, Document).join(
            Document, Document.id == DocumentChunk.document_id).where(
            Document.user_id == uid, Document.project.in_(["", project]))
        if document_ids is not None:
            statement = statement.where(Document.id.in_(document_ids))
        ranked = []
        for chunk, document in self.db.execute(statement):
            score = sum(word in chunk.content.casefold() for word in words)
            if score:
                ranked.append((score, document.id, chunk.position, {
                    "document_id": document.id, "chunk_id": chunk.id,
                    "filename": document.filename, "project": document.project,
                    "position": chunk.position, "text": chunk.content,
                }))
        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [row[3] for row in ranked[:5]]

    def context(self, uid, query, project, document_ids):
        sources = self.search(uid, query, project, document_ids)
        bounded = []
        for source in sources:
            if len(json.dumps(bounded + [source], ensure_ascii=False)) > 4500:
                break
            bounded.append(source)
        return json.dumps(bounded, ensure_ascii=False), bounded


def documents_router(authenticated, repository):
    router = APIRouter(prefix="/api/documents", tags=["documents"])

    @router.get("")
    def listing(actor=Depends(authenticated), repo=Depends(repository)):
        return [document_dict(d) for d in DocumentRepository(repo.db).list(actor[0].id)]

    @router.post("", status_code=201)
    def upload(body: DocumentInput, actor=Depends(authenticated), repo=Depends(repository)):
        uid = actor[0].id
        item, duplicate = DocumentRepository(repo.db).create(uid, body)
        repo.db.commit()
        return {**document_dict(item), "duplicate": duplicate}

    @router.get("/search")
    def search(q: str = "", project: str = "", actor=Depends(authenticated),
               repo=Depends(repository)):
        if len(q) > 200 or len(project) > 80:
            raise HTTPException(422, "검색어 또는 프로젝트 이름이 너무 깁니다.")
        return {"mode": "keyword", "sources": DocumentRepository(repo.db).search(
            actor[0].id, q, project)}

    @router.get("/{document_id}")
    def detail(document_id: str, actor=Depends(authenticated), repo=Depends(repository)):
        item = DocumentRepository(repo.db).get(actor[0].id, document_id)
        return {**document_dict(item), "content": item.content}

    @router.get("/{document_id}/export")
    def export(document_id: str, actor=Depends(authenticated), repo=Depends(repository)):
        item = DocumentRepository(repo.db).get(actor[0].id, document_id)
        return Response(item.content, media_type="text/plain",
                        headers={"Content-Disposition": 'attachment; filename="role-ai-document.txt"'})

    @router.delete("/{document_id}", status_code=204)
    def remove(document_id: str, actor=Depends(authenticated), repo=Depends(repository)):
        item = DocumentRepository(repo.db).get(actor[0].id, document_id)
        repo.db.delete(item)
        repo.db.commit()

    return router
