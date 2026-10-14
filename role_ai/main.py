import asyncio
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from role_ai.assistant import AssistantContext, AssistantManager
from role_ai.billing import BillingLedger
from role_ai.body_limit import BodyLimitMiddleware
from role_ai.config import Settings
from role_ai.database import Base, build_database
from role_ai.providers import ProviderManager, ProviderUnavailable
from role_ai.repositories import ConcurrentTurn, Repository
from role_ai.schemas import ChatRequest, ConversationCreate, Credentials, ProviderName
from role_ai.security import RateLimiter, hash_password, token_hash, verify_password
from role_ai.skills import SKILLS, system_prompt

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


def create_app(settings: Settings | None = None, providers=None, gateway_transport=None, openai_transport=None):
    settings = settings or Settings()
    engine, factory = build_database(settings.database_url)
    ledger = BillingLedger(settings, factory)
    providers = providers or ProviderManager(settings, ledger=ledger, openai_transport=openai_transport)
    assistant = AssistantManager(settings, providers, gateway_transport)
    turn_locks = [asyncio.Lock() for _ in range(32)]
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
    app.state.billing = ledger

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

    async def exclusive_turn(conversation_id: str, actor=Depends(authenticated)):
        key = actor[0].id + ":" + conversation_id
        index = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(turn_locks)
        lock = turn_locks[index]
        if lock.locked():
            raise HTTPException(409, "진행 중인 대화 요청이 있습니다. 완료 후 다시 시도하세요.")
        async with lock:
            yield

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
        catalog = providers.catalog()
        paid_available = actor[0].is_owner and providers.get("openai").configured()
        for item in catalog:
            if item["id"] == "openai" and not paid_available:
                item.update(usable=False, paid_calls=False, state="disabled")
        return {
            "phase": 1, "assistant_engine": "provider_fallback",
            "openclaw": ("not_verified" if assistant.available(actor[0].is_owner) else "disabled"),
            "openclaw_available": assistant.available(actor[0].is_owner),
            "tools": "disabled",
            "default_provider": settings.default_provider, "providers": catalog,
            "skills": [{"id": key, "label": value[0]} for key, value in SKILLS.items()],
            "paid_api_calls": "opt_in" if paid_available else "disabled",
        }

    @app.post("/api/providers/{provider}/test")
    async def provider_test(provider: ProviderName, actor=Depends(authenticated)):
        if provider == "openai" and not actor[0].is_owner:
            raise HTTPException(403, "OpenAI는 소유자 전용입니다.")
        if not chat_limiter.allow(actor[0].id):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
        try:
            return await providers.get(provider).probe()
        except ProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/usage")
    def usage(actor=Depends(authenticated)):
        if not actor[0].is_owner:
            raise HTTPException(403, "사용량 조회는 소유자 전용입니다.")
        return {"currency": "USD", "period": "UTC day", "records": ledger.history(actor[0].id)}

    @app.post("/api/conversations/{conversation_id}/quote")
    def quote(
        conversation_id: str, body: ChatRequest, actor=Depends(authenticated),
        repo: Repository = Depends(repository),
    ):
        user = actor[0]
        if not user.is_owner:
            raise HTTPException(403, "OpenAI는 소유자 전용입니다.")
        items = repo.messages(user.id, conversation_id)
        if items is None:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        if body.engine != "provider_fallback" or body.provider != "openai":
            raise HTTPException(422, "OpenAI 기본 비서 호출만 비용 조회를 지원합니다.")
        if not chat_limiter.allow(user.id):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
        prompt = [{"role": "system", "content": system_prompt(body.skill)}]
        history = [{"role": m.role, "content": m.content} for m in items]
        prompt += (history + [{"role": "user", "content": body.content}])[-20:]
        try:
            return ledger.quote(prompt)
        except ProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post("/api/assistant/openclaw/test")
    async def gateway_test(actor=Depends(authenticated)):
        user = actor[0]
        if not user.is_owner or settings.mode != "local":
            raise HTTPException(403, "OpenClaw는 로컬 소유자 전용입니다.")
        if not chat_limiter.allow(user.id):
            raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
        try:
            return await assistant.openclaw.probe(AssistantContext(user.id, "probe", True))
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
        turn=Depends(exclusive_turn),
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
        if body.engine == "openclaw" and (not user.is_owner or settings.mode != "local"):
            raise HTTPException(403, "OpenClaw는 로컬 소유자 전용입니다.")
        if body.engine == "provider_fallback" and (body.provider or settings.default_provider) == "openai":
            if not user.is_owner:
                raise HTTPException(403, "OpenAI는 소유자 전용입니다.")
            if not body.allow_paid:
                raise HTTPException(403, "이 요청의 유료 API 사용에 동의해야 합니다.")
        revision = conversation.revision
        history = [{"role": m.role, "content": m.content}
                   for m in repo.messages(user.id, conversation_id)]
        # Release the read transaction before waiting for the model.
        repo.db.rollback()
        try:
            result = await assistant.reply(
                body.engine,
                AssistantContext(user.id, conversation_id, user.is_owner, body.allow_paid),
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
            "assistant_engine": body.engine, "tools": [],
            "billing": {"calculated_usd": result.cost_usd, "state": result.billing_state},
        }

    return app
