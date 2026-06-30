"""Report generation utilities."""

import json
from pathlib import Path

from domain.reports import DailyInvestmentReport, InvestmentThesis, MarketReport


class ReportWriter:
    def __init__(self, output_dir: str = "./reports/output") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def write_thesis(self, thesis: InvestmentThesis) -> Path:
        path = self._output_dir / f"thesis_{thesis.ticker}_{thesis.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(thesis.model_dump_json(indent=2), encoding="utf-8")
        return path

    def write_market_report(self, report: MarketReport) -> Path:
        path = self._output_dir / f"market_{report.session.value}_{report.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return path

    def write_daily_report(self, report: DailyInvestmentReport) -> Path:
        path = self._output_dir / f"daily_{report.date.strftime('%Y%m%d')}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return path

    @staticmethod
    def to_markdown_thesis(thesis: InvestmentThesis) -> str:
        lines = [
            f"# Investment Thesis: {thesis.ticker}",
            "",
            "## Executive Summary",
            thesis.executive_summary,
            "",
            "## Recommendation",
            f"**{thesis.recommendation.value.replace('_', ' ').upper()}** (Confidence: {thesis.confidence:.0%})",
            "",
            "## Investment Thesis",
            thesis.investment_thesis,
            "",
            "## Scenarios",
            f"- Bull ({thesis.bull_case.probability:.0%}): {thesis.bull_case.thesis}",
            f"- Base ({thesis.base_case.probability:.0%}): {thesis.base_case.thesis}",
            f"- Bear ({thesis.bear_case.probability:.0%}): {thesis.bear_case.thesis}",
            "",
            "## Key Risks",
        ]
        for risk in thesis.risks[:5]:
            lines.append(f"- [{risk.category.value}] {risk.statement}")
        lines.extend(["", "## Catalysts"])
        for cat in thesis.catalysts[:5]:
            lines.append(f"- {cat.statement}")
        return "\n".join(lines)
