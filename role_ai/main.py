import hmac
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from role_ai.assistant import AssistantContext, ProviderAssistant
from role_ai.body_limit import BodyLimitMiddleware
from role_ai.config import Settings
from role_ai.database import Base, build_database
from role_ai.providers import ProviderManager, ProviderUnavailable
from role_ai.repositories import ConcurrentTurn, Repository
from role_ai.schemas import ChatRequest, ConversationCreate, Credentials, ProviderName
from role_ai.security import RateLimiter, hash_password, token_hash, verify_password
from role_ai.skills import SKILLS

COOKIE = "role_ai_session"
STATIC = Path(__file__).parent / "static"


def conversation_dict(item):
    return {"id": item.id, "title": item.title, "created_at": item.created_at}


def message_dict(item):
    return {
        "id": item.id, "role": item.role, "content": item.content,
        "provider": item.provider, "model": item.model, "skill": item.skill,
        "created_at": item.created_at,
    }


def create_app(settings: Settings | None = None, providers=None):
    settings = settings or Settings()
    engine, factory = build_database(settings.database_url)
    providers = providers or ProviderManager(settings)
    assistant = ProviderAssistant(providers)
    auth_limiter = RateLimiter(maximum=10)
    chat_limiter = RateLimiter(maximum=20)
    # Dummy hash equalizes unknown-user and wrong-password verification work.
    dummy_hash = hash_password(secrets.token_urlsafe(24))

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        if settings.bootstrap_email:
            with factory() as db:
                repo = Repository(db)
                email = settings.bootstrap_email.strip().lower()
                if not repo.user_by_email(email):
                    repo.create_user(
                        email, hash_password(settings.bootstrap_password.get_secret_value()),
                        owner=True,
                    )
        yield
        engine.dispose()

    app = FastAPI(title="Role-AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.state.settings = settings
    app.state.database_factory = factory
    app.state.providers = providers

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            origin is not None and origin not in settings.allowed_origins
        ):
            return JSONResponse({"detail": "허용되지 않은 출처입니다."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def repository():
        with factory() as db:
            yield Repository(db)

    def authenticated(request: Request, repo: Repository = Depends(repository)):
        raw_token = request.cookies.get(COOKIE, "")
        session = repo.auth_session(token_hash(raw_token)) if raw_token else None
        if not session:
            raise HTTPException(401, "로그인이 필요합니다.")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", ""), session.csrf_token
        ):
            raise HTTPException(403, "요청 인증 토큰이 올바르지 않습니다.")
        user = repo.user(session.user_id)
        if not user:
            raise HTTPException(401, "로그인이 필요합니다.")
        return user, session

    def limit_auth(request):
        address = request.client.host if request.client else "unknown"
        if not auth_limiter.allow(address):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")

    def issue_session(user, response, repo):
        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        repo.create_auth_session(
            token_hash(raw_token), user.id, csrf_token,
            time.time() + settings.session_ttl_seconds,
        )
        response.set_cookie(
            COOKIE, raw_token, max_age=settings.session_ttl_seconds, httponly=True,
            secure=settings.cookie_secure, samesite="strict", path="/",
        )
        return {"user": {"id": user.id, "email": user.email}, "csrf_token": csrf_token}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "phase": 1}

    @app.get("/api/auth/policy")
    def auth_policy():
        return {"registration_enabled": settings.registration_enabled}

    @app.post("/api/auth/register", status_code=201)
    def register(
        body: Credentials, request: Request, response: Response,
        repo: Repository = Depends(repository),
    ):
        if not settings.registration_enabled:
            raise HTTPException(403, "회원가입이 비활성화되어 있습니다.")
        limit_auth(request)
        try:
            user = repo.create_user(body.email, hash_password(body.password))
        except IntegrityError:
            repo.db.rollback()
            raise HTTPException(409, "이 계정으로 가입할 수 없습니다.") from None
        return issue_session(user, response, repo)

    @app.post("/api/auth/login")
    def login(
        body: Credentials, request: Request, response: Response,
        repo: Repository = Depends(repository),
    ):
        limit_auth(request)
        user = repo.user_by_email(body.email)
        valid = verify_password(body.password, user.password_hash if user else dummy_hash)
        if not user or not valid:
            raise HTTPException(401, "이메일 또는 비밀번호를 확인해 주세요.")
        # Replace this browser's previous session when signing in again.
        previous = request.cookies.get(COOKIE)
        if previous:
            repo.logout(token_hash(previous))
        return issue_session(user, response, repo)

    @app.get("/api/auth/me")
    def me(actor=Depends(authenticated)):
        user, session = actor
        return {"user": {"id": user.id, "email": user.email},
                "csrf_token": session.csrf_token}

    @app.post("/api/auth/logout", status_code=204)
    def logout(
        response: Response, actor=Depends(authenticated),
        repo: Repository = Depends(repository),
    ):
        repo.logout(actor[1].token_hash)
        response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure,
                               httponly=True, samesite="strict")

    @app.get("/api/status")
    def status(actor=Depends(authenticated)):
        return {
            "phase": 1, "assistant_engine": "provider_fallback",
            "openclaw": "not_implemented", "tools": "disabled",
            "default_provider": settings.default_provider, "providers": providers.catalog(),
            "skills": [{"id": key, "label": value[0]} for key, value in SKILLS.items()],
            "paid_api_calls": "disabled",
        }

    @app.post("/api/providers/{provider}/test")
    async def provider_test(provider: ProviderName, actor=Depends(authenticated)):
        if not chat_limiter.allow(actor[0].id):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
        try:
            return await providers.get(provider).probe()
        except ProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/conversations")
    def conversations(actor=Depends(authenticated), repo: Repository = Depends(repository)):
        return [conversation_dict(c) for c in repo.conversations(actor[0].id)]

    @app.post("/api/conversations", status_code=201)
    def create_conversation(
        body: ConversationCreate, actor=Depends(authenticated),
        repo: Repository = Depends(repository),
    ):
        if not chat_limiter.allow(actor[0].id):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
        return conversation_dict(repo.create_conversation(actor[0].id, body.title))

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(
        conversation_id: str, actor=Depends(authenticated),
        repo: Repository = Depends(repository),
    ):
        items = repo.messages(actor[0].id, conversation_id)
        if items is None:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        return [message_dict(item) for item in items]

    @app.post("/api/conversations/{conversation_id}/chat")
    async def chat(
        conversation_id: str, body: ChatRequest, actor=Depends(authenticated),
        repo: Repository = Depends(repository),
    ):
        user = actor[0]
        conversation = repo.conversation(user.id, conversation_id)
        if not conversation:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        if conversation.revision >= 500:
            raise HTTPException(409, "대화가 길어졌습니다. 새 대화를 시작해 주세요.")
        if not chat_limiter.allow(user.id):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
        revision = conversation.revision
        history = [{"role": m.role, "content": m.content}
                   for m in repo.messages(user.id, conversation_id)]
        # Release the read transaction before waiting for the model.
        repo.db.rollback()
        try:
            result = await assistant.reply(
                AssistantContext(user.id, conversation_id, user.is_owner),
                history + [{"role": "user", "content": body.content}],
                body.provider or settings.default_provider, body.skill,
            )
        except ProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        try:
            repo.append_turn(user.id, conversation_id, revision, body.content, result, body.skill)
        except ConcurrentTurn:
            raise HTTPException(409, "동시에 다른 메시지가 저장됐습니다. 다시 시도해 주세요.") from None
        return {
            "content": result.text, "provider": result.provider, "model": result.model,
            "skill": body.skill, "simulated": result.simulated,
            "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
            "assistant_engine": "provider_fallback", "tools": [],
        }

    return app
