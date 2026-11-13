"""REST output port."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Query

from dpcp.ports import a2a
from dpcp.ports.service import Denied, NotFound, ProductService


def create_app(service: ProductService) -> FastAPI:
    app = FastAPI(title="Data products", version="1.0.0")

    def who(authorization: str | None):
        try:
            return service.principal((authorization or "").removeprefix("Bearer ").strip())
        except Denied as e:
            raise HTTPException(401, str(e)) from e

    @app.get("/products")
    def products(authorization: str | None = Header(default=None)):
        return service.catalogue(who(authorization))

    @app.get("/products/{product_id}")
    def describe(product_id: str, authorization: str | None = Header(default=None)):
        try:
            return service.describe(who(authorization), product_id)
        except NotFound as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/products/{product_id}/rows")
    def rows(
        product_id: str,
        columns: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        authorization: str | None = Header(default=None),
    ):
        try:
            return service.read(
                who(authorization), product_id, "api", columns=columns.split(",") if columns else None, limit=limit
            )
        except Denied as e:
            raise HTTPException(403, str(e)) from e
        except NotFound as e:
            raise HTTPException(404, str(e)) from e

    a2a.mount(app, service)
    return app
