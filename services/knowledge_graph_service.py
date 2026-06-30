"""Financial knowledge graph — build, traverse, impact analysis."""

from __future__ import annotations

import networkx as nx

from data.knowledge_graph_seed import EDGES, NODES
from domain.knowledge_graph import (
    GraphEdge,
    GraphNode,
    ImpactPath,
    KnowledgeGraphView,
    NodeType,
)
from utils.logging import get_logger

logger = get_logger(__name__)


class KnowledgeGraphService:
    def __init__(self) -> None:
        self._g = nx.DiGraph()
        self._nodes: dict[str, GraphNode] = {}
        self._build()

    def _build(self) -> None:
        for node in NODES:
            self._nodes[node.id] = node
            self._g.add_node(node.id, **node.model_dump())
        for edge in EDGES:
            self._g.add_edge(
                edge.source,
                edge.target,
                relation=edge.relation,
                impact=edge.impact,
                strength=edge.strength,
            )

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id.upper()) or self._nodes.get(node_id)

    def resolve_ticker_node(self, ticker: str) -> str | None:
        t = ticker.upper()
        if t in self._nodes:
            return t
        for nid, node in self._nodes.items():
            if node.proxy_ticker and node.proxy_ticker.upper() == t:
                return nid
        return None

    def subgraph_for_ticker(self, ticker: str, depth: int = 2) -> KnowledgeGraphView:
        center = self.resolve_ticker_node(ticker) or ticker.upper()
        if center not in self._g:
            # Attach orphan ticker node
            orphan = GraphNode(id=center, type=NodeType.COMPANY, label=center, sector="Unknown")
            self._nodes[center] = orphan
            self._g.add_node(center, **orphan.model_dump())

        # BFS neighborhood
        visited: set[str] = {center}
        frontier = {center}
        all_edges: list[GraphEdge] = []

        for _ in range(depth):
            next_frontier: set[str] = set()
            for nid in frontier:
                for pred in self._g.predecessors(nid):
                    if pred not in visited:
                        next_frontier.add(pred)
                    ed = self._g.get_edge_data(pred, nid) or {}
                    all_edges.append(
                        GraphEdge(
                            source=pred,
                            target=nid,
                            relation=ed.get("relation", "linked"),
                            impact=ed.get("impact", "neutral"),
                            strength=ed.get("strength", 0.5),
                        )
                    )
                for succ in self._g.successors(nid):
                    if succ not in visited:
                        next_frontier.add(succ)
                    ed = self._g.get_edge_data(nid, succ) or {}
                    all_edges.append(
                        GraphEdge(
                            source=nid,
                            target=succ,
                            relation=ed.get("relation", "linked"),
                            impact=ed.get("impact", "neutral"),
                            strength=ed.get("strength", 0.5),
                        )
                    )
            visited.update(next_frontier)
            frontier = next_frontier

        nodes = [self._nodes[n] for n in visited if n in self._nodes]
        upstream = [self._nodes[p] for p in self._g.predecessors(center) if p in self._nodes]
        downstream = [self._nodes[s] for s in self._g.successors(center) if s in self._nodes]

        beneficiaries, at_risk = self._classify_neighbors(center)
        paths = self._impact_paths_from(center, max_depth=4)

        center_node = self._nodes.get(center)
        summary = (
            f"Knowledge graph for {center}: {len(upstream)} upstream drivers, "
            f"{len(downstream)} downstream dependents. "
            f"Beneficiaries if positive shock: {', '.join(beneficiaries[:5]) or 'n/a'}. "
            f"At risk: {', '.join(at_risk[:5]) or 'n/a'}."
        )

        return KnowledgeGraphView(
            ticker=ticker.upper(),
            center_node=center,
            nodes=nodes,
            edges=all_edges,
            upstream=upstream,
            downstream=downstream,
            impact_paths=paths,
            beneficiaries=beneficiaries,
            at_risk=at_risk,
            summary=summary,
        )

    def _classify_neighbors(self, center: str) -> tuple[list[str], list[str]]:
        beneficiaries: list[str] = []
        at_risk: list[str] = []
        for succ in self._g.successors(center):
            ed = self._g.get_edge_data(center, succ) or {}
            node = self._nodes.get(succ)
            label = node.label if node else succ
            if ed.get("impact") == "positive":
                beneficiaries.append(label)
            elif ed.get("impact") == "negative":
                at_risk.append(label)
        for pred in self._g.predecessors(center):
            ed = self._g.get_edge_data(pred, center) or {}
            node = self._nodes.get(pred)
            label = node.label if node else pred
            if ed.get("impact") == "negative":
                at_risk.append(label)
            elif ed.get("impact") == "positive":
                beneficiaries.append(label)
        return beneficiaries, at_risk

    def _impact_paths_from(self, origin: str, max_depth: int = 4) -> list[ImpactPath]:
        paths: list[ImpactPath] = []
        for target in self._g.nodes:
            if target == origin:
                continue
            try:
                for path in nx.all_simple_paths(self._g, origin, target, cutoff=max_depth):
                    if len(path) < 2:
                        continue
                    relations = []
                    impacts = []
                    for i in range(len(path) - 1):
                        ed = self._g.get_edge_data(path[i], path[i + 1]) or {}
                        relations.append(ed.get("relation", "?"))
                        impacts.append(ed.get("impact", "neutral"))
                    net = "negative" if "negative" in impacts else "positive" if "positive" in impacts else "neutral"
                    node = self._nodes.get(target)
                    ticker = node.proxy_ticker or (target if node and node.type == NodeType.COMPANY else None)
                    paths.append(
                        ImpactPath(
                            origin=origin,
                            path=path,
                            relations=relations,
                            net_impact=net,
                            affected_ticker=ticker,
                            explanation=" → ".join(
                                f"{path[i]} -{relations[i]}-> {path[i+1]}" for i in range(len(relations))
                            ),
                        )
                    )
                    if len(paths) >= 15:
                        return paths
            except nx.NetworkXNoPath:
                continue
        return paths[:15]

    def simulate_shock(self, shock_node: str, direction: str = "negative") -> dict:
        """Simulate shock propagation e.g. hormuz closure or oil spike."""
        node_id = shock_node.lower() if shock_node.lower() in self._nodes else shock_node.upper()
        if node_id not in self._g:
            node_id = self.resolve_ticker_node(shock_node) or shock_node

        beneficiaries: list[str] = []
        at_risk: list[str] = []
        paths_out: list[str] = []

        for succ in nx.descendants(self._g, node_id) if node_id in self._g else []:
            try:
                path = nx.shortest_path(self._g, node_id, succ)
                ed = self._g.get_edge_data(path[-2], path[-1]) if len(path) > 1 else {}
                impact = ed.get("impact", "neutral")
                node = self._nodes.get(succ)
                name = node.label if node else succ
                ticker = node.proxy_ticker if node else (succ if len(succ) <= 5 else None)
                entry = f"{name} ({ticker})" if ticker else name
                if impact == "positive":
                    beneficiaries.append(entry)
                elif impact == "negative":
                    at_risk.append(entry)
                paths_out.append(" → ".join(path))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

        return {
            "shock": node_id,
            "direction": direction,
            "beneficiaries": beneficiaries[:12],
            "at_risk": at_risk[:12],
            "transmission_paths": paths_out[:8],
            "summary": (
                f"Shock at {node_id}: {len(beneficiaries)} potential beneficiaries, "
                f"{len(at_risk)} at risk in dependency graph."
            ),
        }
