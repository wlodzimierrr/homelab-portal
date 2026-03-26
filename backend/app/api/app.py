"""Application-level API router registration."""

from types import ModuleType

from fastapi import FastAPI

from app.api.routes import admin_mutations, auth, catalog, deployments, observability, scaffold, system


def register_api_routes(app: FastAPI, main_module: ModuleType) -> None:
    """Attach the split route modules to the existing FastAPI app instance."""

    system.register_routes(app, main_module)
    auth.register_routes(app, main_module)
    catalog.register_routes(app, main_module)
    deployments.register_routes(app, main_module)
    observability.register_routes(app, main_module)
    scaffold.register_routes(app, main_module)
    admin_mutations.register_routes(app, main_module)
