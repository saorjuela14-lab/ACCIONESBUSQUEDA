"""Financial knowledge graph seed — supply chains, macro links, geopolitical shocks."""

from domain.knowledge_graph import GraphEdge, GraphNode, NodeType

NODES: list[GraphNode] = [
    # Geopolitical / macro
    GraphNode(id="hormuz", type=NodeType.GEOPOLITICAL, label="Strait of Hormuz", description="Critical oil chokepoint"),
    GraphNode(id="opec", type=NodeType.MACRO, label="OPEC+", description="Oil supply cartel"),
    GraphNode(id="fed", type=NodeType.MACRO, label="Federal Reserve", proxy_ticker="TLT"),
    GraphNode(id="rates", type=NodeType.MACRO, label="Interest Rates"),
    GraphNode(id="inflation", type=NodeType.MACRO, label="Inflation"),
    GraphNode(id="usd", type=NodeType.CURRENCY, label="US Dollar", proxy_ticker="UUP"),
    GraphNode(id="china", type=NodeType.COUNTRY, label="China"),
    GraphNode(id="taiwan", type=NodeType.COUNTRY, label="Taiwan"),
    # Commodities
    GraphNode(id="oil", type=NodeType.COMMODITY, label="Crude Oil", proxy_ticker="USO", sector="Energy"),
    GraphNode(id="natgas", type=NodeType.COMMODITY, label="Natural Gas", proxy_ticker="UNG"),
    GraphNode(id="uranium", type=NodeType.COMMODITY, label="Uranium", proxy_ticker="URA"),
    GraphNode(id="copper", type=NodeType.COMMODITY, label="Copper"),
    GraphNode(id="lithium", type=NodeType.COMMODITY, label="Lithium"),
    # Sectors / ETFs
    GraphNode(id="semis", type=NodeType.SECTOR, label="Semiconductors", proxy_ticker="SOXX", sector="Technology"),
    GraphNode(id="airlines", type=NodeType.SECTOR, label="Airlines", proxy_ticker="JETS"),
    GraphNode(id="pharma", type=NodeType.SECTOR, label="Pharma/Biotech", proxy_ticker="XBI", sector="Healthcare"),
    GraphNode(id="nuclear", type=NodeType.SECTOR, label="Nuclear Energy"),
    GraphNode(id="datacenter", type=NodeType.SECTOR, label="Data Center Infrastructure"),
    GraphNode(id="space", type=NodeType.SECTOR, label="Space Economy"),
    GraphNode(id="quantum", type=NodeType.SECTOR, label="Quantum Computing"),
    # Semiconductor chain
    GraphNode(id="TSM", type=NodeType.COMPANY, label="Taiwan Semiconductor", proxy_ticker="TSM", sector="Technology", country="Taiwan"),
    GraphNode(id="NVDA", type=NodeType.COMPANY, label="NVIDIA", proxy_ticker="NVDA", sector="Technology"),
    GraphNode(id="AMD", type=NodeType.COMPANY, label="AMD", proxy_ticker="AMD", sector="Technology"),
    GraphNode(id="ASML", type=NodeType.COMPANY, label="ASML", proxy_ticker="ASML", sector="Technology"),
    GraphNode(id="AVGO", type=NodeType.COMPANY, label="Broadcom", proxy_ticker="AVGO", sector="Technology"),
    GraphNode(id="INTC", type=NodeType.COMPANY, label="Intel", proxy_ticker="INTC", sector="Technology"),
    GraphNode(id="QCOM", type=NodeType.COMPANY, label="Qualcomm", proxy_ticker="QCOM", sector="Technology"),
    # Energy / utilities
    GraphNode(id="XOM", type=NodeType.COMPANY, label="Exxon Mobil", proxy_ticker="XOM", sector="Energy"),
    GraphNode(id="DAL", type=NodeType.COMPANY, label="Delta Air Lines", proxy_ticker="DAL", sector="Industrials"),
    GraphNode(id="UAL", type=NodeType.COMPANY, label="United Airlines", proxy_ticker="UAL", sector="Industrials"),
    GraphNode(id="NEE", type=NodeType.COMPANY, label="NextEra Energy", proxy_ticker="NEE", sector="Utilities"),
  # Watchlist — innovative names
    GraphNode(id="RVMD", type=NodeType.COMPANY, label="Revolution Medicines", proxy_ticker="RVMD", sector="Healthcare"),
    GraphNode(id="ARGX", type=NodeType.COMPANY, label="argenx", proxy_ticker="ARGX", sector="Healthcare"),
    GraphNode(id="VKTX", type=NodeType.COMPANY, label="Viking Therapeutics", proxy_ticker="VKTX", sector="Healthcare"),
    GraphNode(id="ONTO", type=NodeType.COMPANY, label="Onto Innovation", proxy_ticker="ONTO", sector="Technology"),
    GraphNode(id="CAMT", type=NodeType.COMPANY, label="Camtek", proxy_ticker="CAMT", sector="Technology", country="Israel"),
    GraphNode(id="RKLB", type=NodeType.COMPANY, label="Rocket Lab", proxy_ticker="RKLB", sector="Industrials"),
    GraphNode(id="OKLO", type=NodeType.COMPANY, label="Oklo", proxy_ticker="OKLO", sector="Utilities"),
    GraphNode(id="VRT", type=NodeType.COMPANY, label="Vertiv", proxy_ticker="VRT", sector="Industrials"),
    GraphNode(id="IONQ", type=NodeType.COMPANY, label="IonQ", proxy_ticker="IONQ", sector="Technology"),
    GraphNode(id="RNA", type=NodeType.COMPANY, label="Avidity Biosciences", proxy_ticker="RNA", sector="Healthcare"),
    # Infrastructure peers
    GraphNode(id="EQIX", type=NodeType.COMPANY, label="Equinix", proxy_ticker="EQIX", sector="Real Estate"),
    GraphNode(id="CEG", type=NodeType.COMPANY, label="Constellation Energy", proxy_ticker="CEG", sector="Utilities"),
    GraphNode(id="SMR", type=NodeType.COMPANY, label="NuScale Power", proxy_ticker="SMR", sector="Utilities"),
    # Regulation
    GraphNode(id="fda", type=NodeType.REGULATION, label="FDA Drug Approval"),
    GraphNode(id="itar", type=NodeType.REGULATION, label="ITAR Export Controls"),
]

EDGES: list[GraphEdge] = [
    # Oil / Hormuz chain
    GraphEdge(source="hormuz", target="oil", relation="supply_risk", impact="negative", strength=0.95),
    GraphEdge(source="opec", target="oil", relation="supply_control", impact="neutral", strength=0.8),
    GraphEdge(source="oil", target="XOM", relation="revenue_driver", impact="positive", strength=0.85),
    GraphEdge(source="oil", target="airlines", relation="input_cost", impact="negative", strength=0.9),
    GraphEdge(source="oil", target="DAL", relation="fuel_cost", impact="negative", strength=0.85),
    GraphEdge(source="oil", target="UAL", relation="fuel_cost", impact="negative", strength=0.85),
    GraphEdge(source="oil", target="inflation", relation="price_pressure", impact="negative", strength=0.7),
    GraphEdge(source="inflation", target="fed", relation="policy_response", impact="neutral", strength=0.75),
    GraphEdge(source="fed", target="rates", relation="sets", impact="neutral", strength=0.95),
    GraphEdge(source="rates", target="pharma", relation="discount_rate", impact="negative", strength=0.5),
    GraphEdge(source="rates", target="quantum", relation="growth_multiple", impact="negative", strength=0.6),
    # Taiwan / semi chain
    GraphEdge(source="taiwan", target="TSM", relation="headquarters", impact="neutral", strength=0.9),
    GraphEdge(source="china", target="taiwan", relation="geopolitical_risk", impact="negative", strength=0.85),
    GraphEdge(source="china", target="semis", relation="demand_and_risk", impact="neutral", strength=0.7),
    GraphEdge(source="TSM", target="NVDA", relation="foundry_supply", impact="positive", strength=0.95),
    GraphEdge(source="TSM", target="AMD", relation="foundry_supply", impact="positive", strength=0.9),
    GraphEdge(source="TSM", target="QCOM", relation="foundry_supply", impact="positive", strength=0.8),
    GraphEdge(source="ASML", target="TSM", relation="equipment_supply", impact="positive", strength=0.9),
    GraphEdge(source="ASML", target="semis", relation="capex_enabler", impact="positive", strength=0.85),
    GraphEdge(source="NVDA", target="semis", relation="leader", impact="positive", strength=0.95),
    GraphEdge(source="NVDA", target="datacenter", relation="ai_compute_demand", impact="positive", strength=0.95),
    GraphEdge(source="semis", target="ONTO", relation="wafer_inspection", impact="positive", strength=0.75),
    GraphEdge(source="semis", target="CAMT", relation="inspection_equipment", impact="positive", strength=0.8),
    GraphEdge(source="ONTO", target="TSM", relation="customer", impact="positive", strength=0.7),
    GraphEdge(source="CAMT", target="TSM", relation="customer", impact="positive", strength=0.65),
    # Data center / power
    GraphEdge(source="datacenter", target="VRT", relation="cooling_power_infra", impact="positive", strength=0.9),
    GraphEdge(source="datacenter", target="EQIX", relation="colocation_demand", impact="positive", strength=0.8),
    GraphEdge(source="NVDA", target="VRT", relation="power_cooling_demand", impact="positive", strength=0.85),
    GraphEdge(source="natgas", target="datacenter", relation="power_cost", impact="negative", strength=0.5),
    GraphEdge(source="uranium", target="nuclear", relation="fuel_supply", impact="positive", strength=0.9),
    GraphEdge(source="nuclear", target="OKLO", relation="sector_tailwind", impact="positive", strength=0.85),
    GraphEdge(source="nuclear", target="CEG", relation="baseload_power", impact="positive", strength=0.8),
    GraphEdge(source="nuclear", target="SMR", relation="competitor", impact="neutral", strength=0.6),
    GraphEdge(source="OKLO", target="CEG", relation="utility_partnership", impact="positive", strength=0.5),
    GraphEdge(source="rates", target="OKLO", relation="capex_financing", impact="negative", strength=0.55),
    # Space
    GraphEdge(source="space", target="RKLB", relation="launch_demand", impact="positive", strength=0.9),
    GraphEdge(source="itar", target="RKLB", relation="export_compliance", impact="negative", strength=0.5),
  # Quantum
    GraphEdge(source="quantum", target="IONQ", relation="sector_leader", impact="positive", strength=0.85),
    GraphEdge(source="NVDA", target="IONQ", relation="hybrid_compute", impact="positive", strength=0.5),
    GraphEdge(source="fed", target="IONQ", relation="speculative_funding", impact="negative", strength=0.4),
    # Pharma / biotech
    GraphEdge(source="fda", target="pharma", relation="approval_gate", impact="neutral", strength=0.9),
    GraphEdge(source="pharma", target="RVMD", relation="oncology_pipeline", impact="positive", strength=0.8),
    GraphEdge(source="pharma", target="ARGX", relation="rare_disease", impact="positive", strength=0.85),
    GraphEdge(source="pharma", target="VKTX", relation="obesity_nash", impact="positive", strength=0.8),
    GraphEdge(source="pharma", target="RNA", relation="genetic_medicine", impact="positive", strength=0.75),
    GraphEdge(source="fda", target="VKTX", relation="trial_approval", impact="positive", strength=0.7),
    GraphEdge(source="fda", target="RNA", relation="trial_approval", impact="positive", strength=0.7),
    GraphEdge(source="rates", target="RVMD", relation="clinical_capex", impact="negative", strength=0.45),
    # USD / macro
    GraphEdge(source="usd", target="CAMT", relation="fx_exposure", impact="negative", strength=0.4),
    GraphEdge(source="usd", target="ARGX", relation="european_revenue", impact="neutral", strength=0.35),
    # Copper / lithium for infra
    GraphEdge(source="copper", target="VRT", relation="wiring_demand", impact="positive", strength=0.5),
    GraphEdge(source="lithium", target="datacenter", relation="backup_power", impact="positive", strength=0.4),
]
