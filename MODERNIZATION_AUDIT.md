# Modernization / Upgrade Check

_Date checked: 2026-04-25_

## Scope checked
- Runtime/dependency metadata (`requirements.txt`, `runtime.txt`, `pyproject.toml`)
- App structure and APIs (`main.py`, `forms.py`, `db_create.py`)

## Findings

### 1) Dependency management is not currently reproducible
- `requirements.txt` is encoded as UTF-16, which can break tooling that expects UTF-8.
- Dependencies are unpinned (no explicit versions), so builds can drift over time.
- `pyproject.toml` is empty, so the project has no modern package metadata.

**Upgrade recommendation**
- Convert `requirements.txt` to UTF-8.
- Pin versions (or use constraints) and generate a lock strategy (`pip-tools`, Poetry, or `uv`).
- Either populate `pyproject.toml` or remove it to avoid confusion.

### 2) Flask/SQLAlchemy compatibility risk
- The app uses `Users.query.get(...)` and `BlogPost.query.get(...)`, a legacy pattern under SQLAlchemy 2.x migration guidance.

**Upgrade recommendation**
- Move to SQLAlchemy 2-style access, e.g. `db.session.get(Users, id)` and `db.session.get(BlogPost, post_id)`.
- Add a test pass around auth + post detail routes before/after migration.

### 3) Startup pattern is monolithic
- App configuration, models, routes, and initialization all live in `main.py`.
- `db.create_all()` runs in app startup, which can bypass migration workflows.

**Upgrade recommendation**
- Adopt an app-factory layout (`create_app`) with separated modules (`models.py`, `routes.py`, `extensions.py`).
- Rely on Flask-Migrate (`flask db upgrade`) in deployment, not `create_all()` at runtime.

### 4) Security modernization opportunity
- `SECRET_KEY` has a hardcoded fallback value.

**Upgrade recommendation**
- Require `SECRET_KEY` from environment in production and fail fast if missing.
- Document local-dev defaults separately.

### 5) Forms and validation cleanup opportunity
- `Email()` validator import exists but is not used on registration/login fields.

**Upgrade recommendation**
- Add `Email()` validators to relevant fields for stricter validation.

## Suggested phased plan

### Phase 1 (low-risk, immediate)
1. Convert `requirements.txt` to UTF-8 and pin versions.
2. Enforce `Email()` validators in forms.
3. Remove hardcoded production secret fallback.

### Phase 2 (medium-risk)
1. Replace legacy `.query.get(...)` calls with session-based gets.
2. Add smoke tests for register/login/post/comment flows.

### Phase 3 (higher-impact)
1. Refactor to app-factory structure.
2. Split models/routes/extensions into modules.
3. Use migrations-only schema changes in deploy/start scripts.

## Notes from this check
- Live dependency latest-version verification could not be completed from this environment due to outbound package index/network restrictions (HTTP tunnel 403).
