import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from sqlalchemy import func, or_, select, text

from role_ai.models import ApiUsage
from role_ai.providers import ProviderUnavailable


def dollars(microusd):
    return str(Decimal(microusd) / Decimal(1_000_000))


def micro_dollars(value):
    return int((value * Decimal(1_000_000)).to_integral_value(rounding=ROUND_FLOOR))


class BillingLedger:
    """SQLite transaction reserves an estimated budget before any paid network request."""

    def __init__(self, settings, factory):
        self.settings, self.factory = settings, factory

    def ensure_configured(self):
        s = self.settings
        if not (
            s.mode == "local" and s.openai_enabled
            and s.openai_api_key.get_secret_value() and s.openai_model
            and s.openai_input_usd_per_million > 0 and s.openai_output_usd_per_million > 0
            and s.openai_daily_budget_usd > 0 and s.openai_request_budget_usd > 0
        ):
            raise ProviderUnavailable(
                "OpenAI 호출은 로컬 모드의 활성화·API 키·모델·단가·일일/요청 한도가 모두 필요합니다."
            )

    def cost(self, input_tokens, output_tokens, input_rate=None, output_rate=None):
        value = (
            Decimal(input_tokens) * Decimal(input_rate or self.settings.openai_input_usd_per_million)
            + Decimal(output_tokens) * Decimal(output_rate or self.settings.openai_output_usd_per_million)
        )
        # USD per million tokens times tokens equals micro-USD.
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    def estimate(self, messages):
        self.ensure_configured()
        # Conservative text estimate, not an invoice guarantee or exact tokenizer.
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 1024
        return self.cost(input_bound, self.settings.openai_max_output_tokens)

    @staticmethod
    def day_start():
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()

    def used(self, db):
        # Unsettled requests from earlier days remain reserved until reconciled.
        return db.scalar(select(func.coalesce(func.sum(ApiUsage.charged_microusd), 0)).where(
            or_(ApiUsage.created_at >= self.day_start(), ApiUsage.state != "settled")
        ))

    def quote(self, messages):
        amount = self.estimate(messages)
        with self.factory() as db:
            used = self.used(db)
        return {
            "estimated_upper_usd": dollars(amount), "used_or_reserved_usd": dollars(used),
            "daily_budget_usd": str(self.settings.openai_daily_budget_usd),
            "request_budget_usd": str(self.settings.openai_request_budget_usd),
            "within_budget": (
                amount <= micro_dollars(self.settings.openai_request_budget_usd)
                and used + amount <= micro_dollars(self.settings.openai_daily_budget_usd)
            ),
            "currency": "USD", "period": "UTC day", "requires_consent": True,
        }

    def reserve(self, user_id, messages):
        amount = self.estimate(messages)
        if amount > micro_dollars(self.settings.openai_request_budget_usd):
            raise ProviderUnavailable("요청 예상 비용이 설정한 호출 한도를 초과합니다.")
        with self.factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            if self.used(db) + amount > micro_dollars(self.settings.openai_daily_budget_usd):
                raise ProviderUnavailable("OpenAI 일일 예산이 부족합니다.")
            record = ApiUsage(
                user_id=user_id, model=self.settings.openai_model,
                reserved_microusd=amount, charged_microusd=amount,
                input_tokens=None, output_tokens=None,
                input_rate=str(self.settings.openai_input_usd_per_million),
                output_rate=str(self.settings.openai_output_usd_per_million),
            )
            db.add(record)
            db.commit()
            return record.id

    def finish(self, usage_id, usage=None):
        with self.factory() as db:
            record = db.get(ApiUsage, usage_id)
            if usage is not None:
                actual = self.cost(*usage, record.input_rate, record.output_rate)
                record.input_tokens, record.output_tokens = usage
                # Over-estimate failures are recorded honestly; further calls are budget-gated.
                record.charged_microusd = actual
                record.state = "settled"
            else:
                record.state = "uncertain"
            db.commit()
            return dollars(record.charged_microusd), record.state

    def history(self, user_id):
        with self.factory() as db:
            records = list(db.scalars(select(ApiUsage).where(ApiUsage.user_id == user_id)
                                      .order_by(ApiUsage.created_at.desc()).limit(100)))
            return [{
                "id": r.id, "model": r.model, "state": r.state,
                "reserved_usd": dollars(r.reserved_microusd),
                "calculated_usd": dollars(r.charged_microusd),
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                "input_usd_per_million": r.input_rate, "output_usd_per_million": r.output_rate,
                "created_at": r.created_at,
            } for r in records]
