"""Bounded, owner-filtered text retrieval. No paid API or remote embeddings."""
import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text

from role_ai.models import Document, DocumentChunk
from role_ai.pdf_extract import extract_pdf
from role_ai.semantic import SemanticRequest
from role_ai.scope_data import DataAccess, archived, register_source, archive_source, check_tags, require_live_session
from role_ai.models import ScopedResource
from role_ai.scope_models import SourceArchive

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
    def __init__(self, db, session_hash=None):
        self.db = db
        self.session_hash = session_hash

    def get(self, uid, did, project="", manage=False):
        item = self.db.scalar(select(Document).where(
            Document.user_id == uid, Document.id == did))
        access = DataAccess(self.db, uid, project, self.session_hash)
        if not item or not (access.manage('document', item) if manage else access.readable('document', item)):
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        return item

    def create(self, uid, body):
        self.db.rollback()
        self.db.execute(text("BEGIN IMMEDIATE"))
        digest = hashlib.sha256(body.content.encode()).hexdigest()
        existing = self.db.scalar(select(Document).where(
            Document.user_id == uid, Document.project == body.project,
            Document.digest == digest, ~Document.id.in_(select(ScopedResource.document_id).join(
                SourceArchive, SourceArchive.resource_id == ScopedResource.id).where(ScopedResource.kind=="document"))))
        if existing and not archived(self.db, 'document', existing.id):
            register_source(self.db, uid, 'document', existing)
            return existing, True
        count = self.db.scalar(select(func.count()).select_from(Document).where(
            Document.user_id == uid, ~Document.id.in_(select(ScopedResource.document_id).join(
                SourceArchive, SourceArchive.resource_id == ScopedResource.id).where(ScopedResource.kind=='document'))))
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
        register_source(self.db, uid, 'document', item)
        return item, False

    def list(self, uid, project=""):
        access = DataAccess(self.db, uid, project, self.session_hash)
        return [row for row in self.db.scalars(access.query('document').order_by(
            Document.created_at.desc()).limit(MAX_DOCUMENTS)) if access.readable('document', row)]

    def search(self, uid, query, project="", document_ids=None):
        if document_ids is not None:
            for did in set(document_ids):
                item = self.get(uid, did, project)
                if not DataAccess(self.db,uid,project,self.session_hash).readable("document",item):
                    raise HTTPException(404, "현재 프로젝트에서 문서를 찾을 수 없습니다.")
            if not document_ids:
                return []
        words = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))[:20]
        if not words:
            return []
        statement = select(DocumentChunk, Document).join(
            Document, Document.id == DocumentChunk.document_id).where(
            Document.user_id == uid, Document.id.in_([d.id for d in self.list(uid, project)]))
        if document_ids is not None:
            statement = statement.where(Document.id.in_(document_ids))
        ranked = []
        for chunk, document in self.db.execute(statement):
            if chunk.content != document.content[(chunk.position-1)*700:(chunk.position-1)*700+800]:
                raise HTTPException(409,'자료 조각의 무결성을 확인할 수 없습니다.')
            score = sum(word in chunk.content.casefold() for word in words)
            if score:
                ranked.append((score, document.id, chunk.position, {
                    "document_id": document.id, "chunk_id": chunk.id,
                    "filename": document.filename, "project": document.project,
                    "position": chunk.position, "text": chunk.content,
                    **DataAccess(self.db,uid,project,self.session_hash).tag("document", document),
                }))
        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [row[3] for row in ranked[:5]]

    def selected_sources(self, uid, project, document_ids):
        for did in set(document_ids):
            item = self.get(uid, did, project)
            if not DataAccess(self.db,uid,project,self.session_hash).readable("document",item):
                raise HTTPException(404, "현재 프로젝트에서 문서를 찾을 수 없습니다.")
        if not document_ids:
            return []
        query = select(DocumentChunk, Document).join(
            Document, Document.id == DocumentChunk.document_id).where(
            Document.user_id == uid, Document.id.in_(document_ids))
        pairs=list(self.db.execute(query))
        if any(c.content != d.content[(c.position-1)*700:(c.position-1)*700+800] for c,d in pairs):
            raise HTTPException(409,'자료 조각의 무결성을 확인할 수 없습니다.')
        return [{"document_id": d.id, "chunk_id": c.id, "filename": d.filename,
                 "project": d.project, "position": c.position, "text": c.content,
                 **DataAccess(self.db,uid,project,self.session_hash).tag("document",d)}
                for c, d in pairs]

    @staticmethod
    def bound(sources):
        bounded = []
        for source in sources:
            if len(json.dumps(bounded + [source], ensure_ascii=False)) > 4500:
                break
            bounded.append(source)
        return json.dumps(bounded, ensure_ascii=False), bounded

    def context(self, uid, query, project, document_ids):
        sources = self.search(uid, query, project, document_ids)
        return self.bound(sources)


def documents_router(authenticated, repository, semantic):
    router = APIRouter(prefix="/api/documents", tags=["documents"])

    @router.get("")
    def listing(project: str = "", actor=Depends(authenticated), repo=Depends(repository)):
        return [document_dict(d) for d in DocumentRepository(repo.db,actor[1].token_hash).list(actor[0].id,project)]

    @router.post("", status_code=201)
    def upload(body: DocumentInput, actor=Depends(authenticated), repo=Depends(repository)):
        uid = actor[0].id
        item, duplicate = DocumentRepository(repo.db).create(uid, body)
        repo.db.commit()
        return {**document_dict(item), "duplicate": duplicate}

    @router.post("/pdf", status_code=201)
    async def pdf(request: Request, filename: str, project: str = "",
                  actor=Depends(authenticated), repo=Depends(repository)):
        if (not filename.lower().endswith(".pdf") or len(filename) > 120
                or len(project) > 80):
            raise HTTPException(422, "PDF 이름 또는 프로젝트를 확인하세요.")
        try:
            validated = DocumentInput(filename=filename[:-4] + ".txt", content="placeholder",
                                      project=project)
        except ValueError:
            raise HTTPException(422, "PDF 파일 이름이 올바르지 않습니다.") from None
        uid = actor[0].id
        repo.db.rollback()
        from starlette.concurrency import run_in_threadpool
        content = await run_in_threadpool(extract_pdf, await request.body())
        try:
            body = DocumentInput(filename=validated.filename, content=content, project=project)
        except ValueError:
            raise HTTPException(422, "추출 텍스트가 저장 기준을 충족하지 않습니다.") from None
        item, duplicate = DocumentRepository(repo.db).create(uid, body)
        if not duplicate:
            item.filename = filename
        repo.db.commit()
        return {**document_dict(item), "duplicate": duplicate,
                "format": "extracted_pdf_text", "original_retained": False}

    @router.post("/semantic")
    async def semantic_search(body: SemanticRequest, actor=Depends(authenticated),
                              repo=Depends(repository)):
        sources = DocumentRepository(repo.db,actor[1].token_hash).selected_sources(
            actor[0].id, body.project, body.document_ids)
        access=DataAccess(repo.db,actor[0].id,body.project,actor[1].token_hash)
        repo.db.rollback()
        results = await semantic.search(body.query, sources)
        require_live_session(repo.db,actor[1].token_hash,actor[0].id)
        check_tags(repo.db,actor[0].id,access.pid,sources)
        return {"mode": "semantic", "model": semantic.settings.embedding_model,
                "sources": results, "answer_verified": False}

    @router.get("/search")
    def search(q: str = "", project: str = "", actor=Depends(authenticated),
               repo=Depends(repository)):
        if len(q) > 200 or len(project) > 80:
            raise HTTPException(422, "검색어 또는 프로젝트 이름이 너무 깁니다.")
        return {"mode": "keyword", "sources": DocumentRepository(repo.db,actor[1].token_hash).search(
            actor[0].id, q, project)}

    @router.get("/{document_id}")
    def detail(document_id: str, project: str = "", actor=Depends(authenticated), repo=Depends(repository)):
        item = DocumentRepository(repo.db,actor[1].token_hash).get(actor[0].id, document_id, project)
        return {**document_dict(item), "content": item.content}

    @router.get("/{document_id}/export")
    def export(document_id: str, project: str = "", actor=Depends(authenticated), repo=Depends(repository)):
        item = DocumentRepository(repo.db,actor[1].token_hash).get(actor[0].id, document_id, project)
        return Response(item.content, media_type="text/plain",
                        headers={"Content-Disposition": 'attachment; filename="role-ai-document.txt"'})

    @router.delete("/{document_id}", status_code=204)
    def remove(document_id: str, project: str = "", actor=Depends(authenticated), repo=Depends(repository)):
        repo.db.rollback()
        repo.db.execute(text("BEGIN IMMEDIATE"))
        item=repo.db.scalar(select(Document).where(Document.id==document_id,Document.user_id==actor[0].id))
        if not item:
            raise HTTPException(404,"문서를 찾을 수 없습니다.")
        if not DataAccess(repo.db,actor[0].id,project).manage('document',item,include_archived=True):
            raise HTTPException(404,"현재 범위에서 문서를 찾을 수 없습니다.")
        archive_source(repo.db,actor[0].id,'document',item)
        repo.db.commit()
        return Response(status_code=204,headers={'X-Role-AI-Removal':'archived'})

    return router
