"""Investment memory evaluation and agent weight recalibration (daily loop)."""

from config.settings import get_settings
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from providers.interfaces import MarketDataProvider
from services.desk_learning_service import DeskLearningService, classify_error
from utils.logging import get_logger

logger = get_logger(__name__)


class MemoryEvaluationService:
    def __init__(self, memory_repo: InvestmentMemoryRepository, market_provider: MarketDataProvider) -> None:
        self._memory = memory_repo
        self._market = market_provider
        settings = get_settings()
        self._eval_days = settings.memory_evaluation_days
        self._min_hours = float(settings.memory_evaluation_min_hours)
        self._hit_pct = float(settings.memory_hit_pct)

    async def evaluate_pending(self) -> dict:
        pending = await self._memory.list_ready_for_evaluation(
            self._eval_days,
            min_hours=self._min_hours,
        )
        results: dict = {
            "evaluated": 0,
            "correct": 0,
            "incorrect": 0,
            "hit_pct": self._hit_pct,
            "min_hours": self._min_hours,
            "errors": [],
            "lessons": [],
        }

        learning: DeskLearningService | None = None
        session = getattr(self._memory, "_session", None)
        if session is not None:
            learning = DeskLearningService(session)

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
                error_tag = classify_error(record.recommendation, was_correct)
                notes = (
                    f"Entry ${record.entry_price:.2f} → Current ${current_price:.2f} "
                    f"({actual_return:+.1f}%). Recommendation: {record.recommendation}."
                    + (f" Error: {error_tag}." if error_tag else "")
                )

                await self._memory.evaluate(
                    record.id, was_correct, notes, actual_return, error_tag=error_tag
                )
                record.was_correct = was_correct
                record.actual_return_pct = actual_return
                record.error_tag = error_tag
                results["evaluated"] += 1
                if was_correct:
                    results["correct"] += 1
                else:
                    results["incorrect"] += 1
                    results["errors"].append(
                        {
                            "ticker": record.ticker.upper(),
                            "tag": error_tag,
                            "recommendation": record.recommendation,
                            "return_pct": round(actual_return, 2),
                        }
                    )
                    if learning is not None:
                        lesson = await learning.ingest_evaluation(record, was_correct)
                        if lesson:
                            results["lessons"].append(lesson)

                await self._recalibrate_agent_weights(record, was_correct)

            except Exception as exc:
                logger.warning("memory.evaluation.failed", record_id=record.id, error=str(exc))

        if learning is not None:
            try:
                snap = await learning.snapshot()
                results["avoid_tickers"] = snap.get("avoid_tickers") or []
            except Exception as exc:
                logger.warning("memory.evaluation.snapshot_failed", error=str(exc))

        logger.info("memory.evaluation.complete", **{k: v for k, v in results.items() if k != "lessons"})
        return results

    def _was_correct(self, recommendation: str, return_pct: float) -> bool:
        rec = (recommendation or "").lower()
        hit = self._hit_pct
        if rec in ("strong_buy", "buy"):
            return return_pct > hit
        if rec in ("strong_sell", "sell"):
            return return_pct < -hit
        return abs(return_pct) < hit  # hold

    async def _recalibrate_agent_weights(self, record, was_correct: bool) -> None:
        if not get_settings().agent_weights_auto_calibrate:
            return

        # Daily loop: slightly stronger on misses so tomorrow's committee shifts.
        adjustment = 0.04 if was_correct else -0.07
        weights = await self._memory.get_agent_weights()
        if not weights:
            from agents.investment_director import InvestmentDirector
            weights = dict(InvestmentDirector.DEFAULT_WEIGHTS)

        for agent_name, score in record.scores.items():
            if agent_name not in weights:
                continue
            agent_was_right = (score > 0 and was_correct) or (score < 0 and not was_correct)
            delta = adjustment if agent_was_right else -abs(adjustment) * (0.5 if was_correct else 0.7)
            new_weight = max(0.4, min(1.8, weights.get(agent_name, 1.0) + delta))
            weights[agent_name] = new_weight
            accuracy = 0.62 if agent_was_right else 0.38
            await self._memory.update_agent_weight(agent_name, new_weight, accuracy)
