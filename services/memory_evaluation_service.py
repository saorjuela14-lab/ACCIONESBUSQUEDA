"""Investment memory evaluation and agent weight recalibration."""

from datetime import datetime, timezone

from config.settings import get_settings
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from domain.enums import InvestmentRecommendation
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)


class MemoryEvaluationService:
    def __init__(self, memory_repo: InvestmentMemoryRepository, market_provider: MarketDataProvider) -> None:
        self._memory = memory_repo
        self._market = market_provider
        self._eval_days = get_settings().memory_evaluation_days

    async def evaluate_pending(self) -> dict:
        pending = await self._memory.list_ready_for_evaluation(self._eval_days)
        results = {"evaluated": 0, "correct": 0, "incorrect": 0}

        for record in pending:
            if record.entry_price is None or record.entry_price <= 0:
                continue

            try:
                quote = await self._market.get_quote(record.ticker)
                current_price = float(quote.get("current_price") or 0)
                if not current_price:
                    continue

                actual_return = ((current_price - record.entry_price) / record.entry_price) * 100
                was_correct = self._was_correct(record.recommendation, actual_return)
                notes = (
                    f"Entry ${record.entry_price:.2f} → Current ${current_price:.2f} "
                    f"({actual_return:+.1f}%). Recommendation: {record.recommendation}."
                )

                await self._memory.evaluate(record.id, was_correct, notes, actual_return)
                results["evaluated"] += 1
                if was_correct:
                    results["correct"] += 1
                else:
                    results["incorrect"] += 1

                await self._recalibrate_agent_weights(record, was_correct)

            except Exception as exc:
                logger.warning("memory.evaluation.failed", record_id=record.id, error=str(exc))

        logger.info("memory.evaluation.complete", **results)
        return results

    def _was_correct(self, recommendation: str, return_pct: float) -> bool:
        rec = recommendation.lower()
        if rec in ("strong_buy", "buy"):
            return return_pct > 5.0
        if rec in ("strong_sell", "sell"):
            return return_pct < -5.0
        return abs(return_pct) < 5.0  # hold

    async def _recalibrate_agent_weights(self, record, was_correct: bool) -> None:
        if not get_settings().agent_weights_auto_calibrate:
            return

        adjustment = 0.05 if was_correct else -0.05
        weights = await self._memory.get_agent_weights()
        if not weights:
            from agents.investment_director import InvestmentDirector
            weights = dict(InvestmentDirector.DEFAULT_WEIGHTS)

        for agent_name, score in record.scores.items():
            if agent_name not in weights:
                continue
            agent_was_right = (score > 0 and was_correct) or (score < 0 and not was_correct)
            delta = adjustment if agent_was_right else -adjustment * 0.5
            new_weight = max(0.3, min(2.0, weights.get(agent_name, 1.0) + delta))
            accuracy = 0.6 if agent_was_right else 0.4
            await self._memory.update_agent_weight(agent_name, new_weight, accuracy)
