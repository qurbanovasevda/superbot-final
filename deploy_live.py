"""ASGI entrypoint for the read-only Phase 2 deployment."""
from app.main import create_app

app = create_app(data_mode="live")
