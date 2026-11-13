"""MCP output port: three tools over the same service. One server per authenticated caller."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from dpcp.ports.service import Principal, ProductService


def build(service: ProductService, who: Principal) -> MCPServer:
    server = MCPServer(
        name="data-products",
        instructions=(
            "Governed data products. Call list_products, then describe_product for units and meaning, then "
            "read_product. Values carry the contract version and data date: cite them."
        ),
    )

    @server.tool()
    def list_products() -> list[dict]:
        """Data products you may read, with owner, version and description."""
        return service.catalogue(who)

    @server.tool()
    def describe_product(product_id: str) -> dict:
        """Columns with units and descriptions, the freshness promise and the newest data date."""
        return service.describe(who, product_id)

    @server.tool()
    def read_product(
        product_id: str, columns: list[str] | None = None, filters: dict | None = None, limit: int = 50
    ) -> dict:
        """Rows of a product. filters: equality on columns, e.g. {"country": "DE"}."""
        if who.kind == "agent":
            service.describe(who, product_id)
        return service.read(who, product_id, "mcp", columns=columns, filters=filters, limit=limit)

    return server
