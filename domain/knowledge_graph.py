"""Financial knowledge graph domain models."""

from enum import StrEnum

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    COMPANY = "company"
    COMMODITY = "commodity"
    CURRENCY = "currency"
    MACRO = "macro"
    GEOPOLITICAL = "geopolitical"
    ETF = "etf"
    SECTOR = "sector"
    COUNTRY = "country"
    REGULATION = "regulation"
    EVENT = "event"


class GraphNode(BaseModel):
    id: str
    type: NodeType
    label: str
    proxy_ticker: str | None = None
    sector: str | None = None
    country: str | None = None
    description: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    impact: str  # positive | negative | neutral
    strength: float = Field(ge=0.0, le=1.0)
    description: str | None = None


class ImpactPath(BaseModel):
    """A causal chain from shock to affected entity."""
    origin: str
    path: list[str]
    relations: list[str]
    net_impact: str
    affected_ticker: str | None = None
    explanation: str


class KnowledgeGraphView(BaseModel):
    ticker: str
    center_node: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    upstream: list[GraphNode] = Field(default_factory=list)
    downstream: list[GraphNode] = Field(default_factory=list)
    impact_paths: list[ImpactPath] = Field(default_factory=list)
    beneficiaries: list[str] = Field(default_factory=list)
    at_risk: list[str] = Field(default_factory=list)
    summary: str = ""
