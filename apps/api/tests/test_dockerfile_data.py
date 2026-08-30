from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


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
