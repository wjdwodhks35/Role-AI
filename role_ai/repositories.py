import time

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from role_ai.models import AuthSession, Conversation, Message, User


class ConcurrentTurn(Exception):
    pass


class Repository:
    """User-scoped persistence boundary for future database migrations."""

    def __init__(self, db: Session):
        self.db = db

    def user_by_email(self, email):
        return self.db.scalar(select(User).where(User.email == email))

    def create_user(self, email, password_hash, owner=False):
        user = User(email=email, password_hash=password_hash, is_owner=owner)
        self.db.add(user)
        self.db.commit()
        return user

    def create_auth_session(self, token_hash, user_id, csrf_token, expires_at):
        self.db.execute(delete(AuthSession).where(AuthSession.expires_at <= time.time()))
        self.db.add(AuthSession(
            token_hash=token_hash, user_id=user_id, csrf_token=csrf_token, expires_at=expires_at
        ))
        self.db.commit()

    def auth_session(self, hashed_token):
        return self.db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hashed_token, AuthSession.expires_at > time.time()
            )
        )

    def user(self, user_id):
        return self.db.get(User, user_id)

    def logout(self, hashed_token):
        self.db.execute(delete(AuthSession).where(AuthSession.token_hash == hashed_token))
        self.db.commit()

    def conversations(self, user_id):
        return list(self.db.scalars(
            select(Conversation).where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc()).limit(100)
        ))

    def conversation(self, user_id, conversation_id):
        return self.db.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        ))

    def create_conversation(self, user_id, title):
        conversation = Conversation(user_id=user_id, title=title)
        self.db.add(conversation)
        self.db.commit()
        return conversation

    def messages(self, user_id, conversation_id):
        if not self.conversation(user_id, conversation_id):
            return None
        return list(self.db.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence).limit(1000)
        ))

    def append_turn(self, user_id, conversation_id, revision, content, reply, skill):
        result = self.db.execute(
            update(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.revision == revision,
            ).values(revision=revision + 1)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConcurrentTurn
        self.db.add_all([
            Message(conversation_id=conversation_id, role="user", content=content,
                    sequence=revision * 2, skill=skill),
            Message(conversation_id=conversation_id, role="assistant", content=reply.text,
                    sequence=revision * 2 + 1, provider=reply.provider, model=reply.model,
                    skill=skill),
        ])
        self.db.commit()
