from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PermissionRequest:
    user_id: str
    tool: str
    action: str
    resource: str


@dataclass(frozen=True)
class PermissionDecision:
    state: Literal["denied", "requires_approval", "allowed"]
    reason: str


class PermissionGate:
    """Fail-closed boundary. Phase 1 does not execute any PC or external-service tools."""

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision("denied", "Phase 1에서는 도구 실행이 제공되지 않습니다.")

    def evaluate_scoped_read(self, db, session_hash, contract, resource) -> PermissionDecision:
        """Data read adapter only; evaluate() still denies all external execution."""
        from role_ai.scope_permissions import ScopeReadGate
        return ScopeReadGate(db).evaluate(session_hash, contract, resource)
