"""Etiquetas en español para narrativas de análisis."""

from domain.enums import InvestmentRecommendation

RECOMMENDATION_ES: dict[InvestmentRecommendation, str] = {
    InvestmentRecommendation.STRONG_BUY: "COMPRA FUERTE",
    InvestmentRecommendation.BUY: "COMPRAR",
    InvestmentRecommendation.HOLD: "MANTENER",
    InvestmentRecommendation.SELL: "VENDER",
    InvestmentRecommendation.STRONG_SELL: "VENTA FUERTE",
}


def recommendation_label(rec: InvestmentRecommendation) -> str:
    return RECOMMENDATION_ES.get(rec, rec.value.replace("_", " ").upper())


def bias_label(bias: str) -> str:
    mapping = {"bullish": "alcista", "bearish": "bajista", "neutral": "neutral"}
    return mapping.get((bias or "").lower(), bias or "neutral")


def agent_display_name(agent_name: str) -> str:
    key = (agent_name or "").replace("_agent", "")
    labels = {
        "news": "noticias",
        "technical": "técnico",
        "fundamental": "fundamental",
        "valuation": "valoración",
        "macro": "macro",
        "sentiment": "sentimiento",
        "country_risk": "riesgo país",
        "company_risk": "riesgo empresa",
        "corporate_actions": "acciones corporativas",
        "market_dependency": "dependencias",
        "portfolio": "portafolio",
        "watchlist": "watchlist",
        "alert": "alertas",
        "investment_director": "director de inversiones",
        "investment_memory": "memoria",
    }
    return labels.get(key, key.replace("_", " "))
