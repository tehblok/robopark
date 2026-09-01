import re
import stat
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

API_IMAGE = (
    "python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217"
)
WEB_BUILD_IMAGE = (
    "node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf"
)
WEB_RUNTIME_IMAGE = (
    "nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de"
)
OPS_IMAGE = "docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c"


def test_dockerignore_excludes_ops():
    text = (API_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "data/ops" in text


def test_dockerfile_does_not_copy_whole_data_tree():
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY data ./data" not in text
    assert "emergency_sections.json" in text


def test_dockerfile_uses_entrypoint_workers():
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entry = (API_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "docker-entrypoint.sh" in text
    assert "UVICORN_WORKERS" in entry
    assert "--workers" in entry
    assert "workers=4" in entry


def test_report_attachment_upload_and_persistent_storage_are_aligned():
    nginx = (REPO_ROOT / "apps/web/nginx.conf").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert "client_max_body_size 16m;" in nginx
    assert "REPORT_ATTACHMENTS_DIR: /data/report-attachments" in compose


def test_api_dockerfile_uses_pinned_frozen_runtime_dependencies():
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"FROM {API_IMAGE}" in text
    assert "ARG UV_VERSION=0.11.31" in text
    assert 'pip install --no-cache-dir "uv==${UV_VERSION}"' in text
    assert "COPY pyproject.toml uv.lock ./" in text
    assert "uv sync --frozen --no-dev --no-install-project" in text
    assert "uv sync --frozen --no-dev" in text
    assert "pip install --no-cache-dir . pytest" not in text
    assert "apt-get" not in text
    assert "curl" not in text


def test_api_healthchecks_use_python_stdlib_readiness_probe():
    dockerfile = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    probe = (
        "import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"
    )
    assert probe in dockerfile
    assert probe in compose
    assert "curl" not in compose.split("  web:", maxsplit=1)[0]


def test_web_and_ops_images_are_digest_pinned():
    web = (REPO_ROOT / "apps/web/Dockerfile").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert f"FROM {WEB_BUILD_IMAGE} AS build" in web
    assert f"FROM {WEB_RUNTIME_IMAGE}" in web
    assert f"image: {OPS_IMAGE}" in compose


def test_ci_uses_only_the_canonical_verification_entrypoint():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    expected_actions = {
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "astral-sh/setup-uv@e58605a9b6da7c637471fab8847a5e5a6b8df081",
        "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
    }
    actions = set(re.findall(r"uses:\s+([^\s]+)", workflow))
    assert actions == expected_actions
    assert all(re.search(r"@[0-9a-f]{40}$", action) for action in actions)
    assert "version: 0.11.31" in workflow
    assert "node-version: 24.18.0" in workflow
    assert "timeout-minutes: 30" in workflow
    assert re.findall(r"^\s+run:\s+(.+)$", workflow, flags=re.MULTILINE) == ["./scripts/verify.sh"]
    assert "uv pip install" not in workflow


def test_verification_script_has_frozen_targets_and_is_executable():
    script_path = REPO_ROOT / "scripts/verify.sh"
    text = script_path.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\nset -eu\n")
    assert "uv sync --frozen --extra dev" in text
    assert "uv run --frozen --extra dev ruff check ." in text
    assert "uv run --frozen --extra dev ruff format --check ." in text
    assert "python -m pytest -p no:cacheprovider -q" in text
    assert "npm ci" in text
    assert "npm run lint" in text
    assert "npm run build" in text
    assert "npm test" in text
    assert "npm run check-nav" in text
    assert "docker is required for the docker verification target" in text
    assert script_path.stat().st_mode & stat.S_IXUSR


def test_root_gitignore_tracks_api_lockfile():
    ignored_lines = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "uv.lock" not in ignored_lines
