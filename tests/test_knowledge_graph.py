"""Knowledge graph tests."""

from services.knowledge_graph_service import KnowledgeGraphService


def test_subgraph_for_tsm():
    svc = KnowledgeGraphService()
    view = svc.subgraph_for_ticker("TSM", depth=2)
    assert view.center_node == "TSM"
    assert len(view.nodes) > 1
    assert any(n.id == "NVDA" for n in view.nodes) or len(view.downstream) > 0


def test_shock_hormuz():
    svc = KnowledgeGraphService()
    result = svc.simulate_shock("hormuz")
    assert "beneficiaries" in result
    assert "at_risk" in result
    assert len(result["at_risk"]) > 0 or len(result["beneficiaries"]) > 0
