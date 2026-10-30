"""Enforce live task authority at each bounded operation. No default host executor."""

import json
from role_ai.task_approvals import TaskApprovalGate, ApprovalDenied, validate_resource, actor
from role_ai.task_contracts import contract_digest
from role_ai.coding_tasks import FileChange, safe_path
from role_ai.coding_jobs import TaskCreate
from role_ai.coding_workspace import git, MAX_FILE_BYTES
from role_ai.scoped_reads import ScopedReads


class TaskToolDispatcher:
    def __init__(self, db, settings, *, sandbox_handler=None):
        self.db, self.settings, self.sandbox_handler = db, settings, sandbox_handler
        self.gate = TaskApprovalGate(db, settings)

    def dispatch(self, session_hash, contract, tool, resource, arguments=None):
        contract, resource = self.gate.require(session_hash, contract, tool, resource)
        arguments = {} if arguments is None else arguments
        if type(arguments) is not dict:
            raise ApprovalDenied("invalid_arguments")
        if tool in {"documents.read", "memory.read"}:
            if arguments:
                raise ApprovalDenied("invalid_arguments")
            row = ScopedReads(self.db, session_hash, contract.project_id).require(
                resource.kind, resource.resource_id
            )
            result = {"resource_id": row.id, "version": resource.version, "content": row.content}
        elif tool == "coding.read":
            if set(arguments) != {"path"} or arguments["path"] not in resource.paths:
                raise ApprovalDenied("path_denied")
            path = safe_path(arguments["path"])
            user = actor(self.db, session_hash)
            source = validate_resource(
                self.db, self.settings, session_hash, user, contract, resource
            )
            object_id = resource.version + ":" + path
            size = int(git(source, "cat-file", "-s", object_id))
            if size > MAX_FILE_BYTES:
                raise ApprovalDenied("read_budget_exhausted")
            raw = git(source, "show", object_id)
            if len(raw) > MAX_FILE_BYTES:
                raise ApprovalDenied("read_budget_exhausted")
            result = {"path": path, "content": raw.decode("utf-8")}
        elif tool == "coding.propose":
            if set(arguments) != {"changes"} or type(arguments["changes"]) is not list:
                raise ApprovalDenied("invalid_arguments")
            changes = tuple(
                FileChange.model_validate_json(json.dumps(x)) for x in arguments["changes"]
            )
            if not changes or len(changes) > contract.budget.max_files:
                raise ApprovalDenied("patch_budget_exhausted")
            if any(c.path not in resource.paths for c in changes):
                raise ApprovalDenied("path_denied")
            # Reuse the original test/config/security path exclusion, never approve model edits implicitly.
            TaskCreate(
                repository_id=resource.resource_id,
                project_id=contract.project_id,
                request=contract.goal,
                allowed_files=tuple(c.path for c in changes),
            )
            if (
                sum(len((c.content or "").encode()) for c in changes)
                > contract.budget.max_patch_bytes
            ):
                raise ApprovalDenied("patch_budget_exhausted")
            result = {
                "changes": [c.model_dump(mode="json") for c in changes],
                "applied": False,
                "contract_digest": contract_digest(contract),
            }
        elif tool == "coding.sandbox_run":
            if set(arguments) != {"command"} or arguments["command"] not in {"unittest", "pytest"}:
                raise ApprovalDenied("command_denied")
            if self.sandbox_handler is None:
                raise ApprovalDenied("runner_unavailable")
            # Registered by trusted application code only, not supplied by HTTP/model arguments.
            result = self.sandbox_handler(contract, resource, arguments["command"])
        else:
            raise ApprovalDenied("tool_denied")
        # A decision is not a reusable capability. Revocation/expiry while running discards the result.
        self.gate.require(session_hash, contract, tool, resource)
        return result
