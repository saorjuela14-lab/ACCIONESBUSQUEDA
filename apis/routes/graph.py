"""Knowledge graph API routes."""

from fastapi import APIRouter, Query

from domain.knowledge_graph import KnowledgeGraphView
from services.knowledge_graph_service import KnowledgeGraphService

router = APIRouter()
_graph = KnowledgeGraphService()


@router.get("/graph/{ticker}", response_model=KnowledgeGraphView)
async def get_knowledge_graph(
    ticker: str,
    depth: int = Query(default=2, ge=1, le=4),
) -> KnowledgeGraphView:
    return _graph.subgraph_for_ticker(ticker.upper(), depth=depth)


@router.get("/graph/shock/{node_id}")
async def simulate_shock(node_id: str, direction: str = "negative") -> dict:
    return _graph.simulate_shock(node_id, direction=direction)
