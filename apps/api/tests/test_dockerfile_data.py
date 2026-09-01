from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


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
