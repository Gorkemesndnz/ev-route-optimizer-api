# Project Guidelines

## Code Style
- Keep Python code modular and service-oriented under [app/](app/).
- Prefer small, focused edits in routers, services, and helpers over expanding large entry points.
- Preserve existing naming and data-model patterns in the FastAPI codebase.

## Architecture
- Treat this folder as the route-planning and simulation engine.
- Keep endpoint logic thin in [app/main.py](app/main.py) when a router or service is the better home.
- Refer to [README.md](README.md) and [architectural_blueprint.md](architectural_blueprint.md) for setup and system behavior instead of restating them.

## Build and Test
- Install dependencies with `pip install -r requirements.txt`.
- Run locally with `uvicorn app.main:app --reload --port 8000`.
- Run tests with `pytest tests/ -v`.

## Conventions
- Prefer environment variables and repo-managed config files over hardcoded keys or machine-specific paths.
- Keep route calculation, charging, station selection, and simulation logic in the layer that already owns it.
- Avoid adding duplicate business logic across routers and services.
