import re
import stat
import subprocess
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

API_IMAGE = (
    "python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
)
WEB_BUILD_IMAGE = (
    "node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf"
)
WEB_RUNTIME_IMAGE = (
    "nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94"
)
OPS_IMAGE = "docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c"
VERIFY_SCRIPT = REPO_ROOT / "scripts/verify.sh"
UV_SYNC_RUNS = [
    "RUN uv sync --frozen --no-dev --no-install-project",
    "RUN uv sync --frozen --no-dev",
]


def _uv_sync_runs(dockerfile: Path) -> list[str]:
    return [
        line
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.startswith("RUN uv sync ")
    ]


def _write_fake_tool(fake_bin: Path, name: str, *, log_host_env: bool = False) -> None:
    env_field = (
        """
printf '\\tHOST_ENV_FILE=%s' "${HOST_ENV_FILE-}" >> "$VERIFY_LOG"
if [ "${1-}" = compose ]; then
  for placeholder in \
    "${ROBOPARK_POSTGRES_PASSWORD_FILE-}" \
    "${ROBOPARK_PGPASS_FILE-}" \
    "${ROBOPARK_SNAPSHOT_CONFIG_FILE-}"
  do
    [ -f "$placeholder" ] || exit 91
  done
  printf '\\tSAFE_PLACEHOLDERS_PRESENT=1' >> "$VERIFY_LOG"
fi
"""
        if log_host_env
        else ""
    )
    tool = fake_bin / name
    tool.write_text(
        f"""#!/bin/sh
set -eu
printf '%s' '{name}' >> "$VERIFY_LOG"{env_field}
for arg in "$@"; do
  printf '\\t%s' "$arg" >> "$VERIFY_LOG"
done
printf '\\n' >> "$VERIFY_LOG"
""",
        encoding="utf-8",
    )
    tool.chmod(0o755)


def _write_fake_dirname(fake_bin: Path) -> None:
    tool = fake_bin / "dirname"
    tool.write_text(
        """#!/bin/sh
set -eu
value=$1
case "$value" in
  */*) directory=${value%/*} ;;
  *) directory=. ;;
esac
if [ -z "$directory" ]; then
  directory=/
fi
printf '%s\\n' "$directory"
""",
        encoding="utf-8",
    )
    tool.chmod(0o755)


def _run_verify(
    tmp_path: Path, *args: str, include_docker: bool = True
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_bin = tmp_path / "bin"
    outside_checkout = tmp_path / "outside"
    fake_bin.mkdir(parents=True)
    outside_checkout.mkdir()
    _write_fake_dirname(fake_bin)
    for name in ("uv", "npm", "sh"):
        _write_fake_tool(fake_bin, name)
    if include_docker:
        _write_fake_tool(fake_bin, "docker", log_host_env=True)

    log = tmp_path / "commands.log"
    result = subprocess.run(
        [str(VERIFY_SCRIPT), *args],
        cwd=outside_checkout,
        env={"PATH": str(fake_bin), "VERIFY_LOG": str(log)},
        text=True,
        capture_output=True,
        check=False,
    )
    commands = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, commands


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


def test_web_document_and_terminal_headers_scope_browser_capabilities():
    nginx = (REPO_ROOT / "apps/web/nginx.conf").read_text(encoding="utf-8")
    headers = [
        line.strip().split('"', 2)[1]
        for line in nginx.splitlines()
        if line.strip().startswith("add_header Permissions-Policy ")
    ]
    document_policy = "geolocation=(), microphone=(), camera=(self)"
    terminal_policy = (
        "clipboard-read=(), clipboard-write=(), geolocation=(), microphone=(), camera=()"
    )

    assert headers.count(document_policy) == 4
    assert headers.count(terminal_policy) == 1
    assert len(headers) == 5

    terminal_location = nginx.split("location = /terminal.html {", 1)[1].split("\n    }", 1)[0]
    assert f'add_header Permissions-Policy "{terminal_policy}" always;' in terminal_location


def test_api_dockerfile_uses_pinned_frozen_runtime_dependencies():
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"FROM {API_IMAGE}" in text
    assert "ARG UV_VERSION=0.11.31" in text
    assert 'pip install --no-cache-dir "uv==${UV_VERSION}"' in text
    assert "COPY pyproject.toml uv.lock ./" in text
    assert "pip install --no-cache-dir . pytest" not in text
    assert "apt-get install -y --no-install-recommends postgresql-client-17" in text
    assert "rm -rf /var/lib/apt/lists/*" in text
    assert "curl" not in text


def test_api_image_trusts_yandex_internal_root_ca():
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    certificate = API_ROOT / "src/robopark_api/certificates/YandexInternalRootCA.crt"

    assert certificate.read_text(encoding="ascii").startswith("-----BEGIN CERTIFICATE-----")
    assert "COPY src ./src" in text
    assert "update-ca-certificates" not in text
    assert "SSL_CERT_FILE" not in text


def test_api_dockerfile_runs_exact_ordered_production_sync_phases():
    assert _uv_sync_runs(API_ROOT / "Dockerfile") == UV_SYNC_RUNS


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


def test_ci_uses_only_the_pinned_verification_entrypoints():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    expected_actions = {
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "astral-sh/setup-uv@e58605a9b6da7c637471fab8847a5e5a6b8df081",
        "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    }
    actions = set(re.findall(r"uses:\s+([^\s]+)", workflow))
    assert actions == expected_actions
    assert all(re.search(r"@[0-9a-f]{40}$", action) for action in actions)
    assert "version: 0.11.31" in workflow
    assert "node-version: 24.18.0" in workflow
    assert "timeout-minutes: 90" in workflow
    assert "shard: [1, 2, 3, 4]" in workflow
    assert "needs: [verify-core, responsive, browser-workflows]" in workflow
    assert "if: ${{ always() }}" in workflow
    assert 'job["result"] != "success"' in workflow
    assert re.findall(r"^[ \t]+(?:- )?run:[ \t]+(.+)$", workflow, flags=re.MULTILINE) == [
        "python3 scripts/check-tech-debt.py && python3 scripts/check-module-boundaries.py",
        "./scripts/verify.sh",
        "sh scripts/audit-dependencies.sh",
        "|",
        "npm ci",
        "npm run test:e2e:linux -- --workers=2 --shard=${{ matrix.shard }}/4",
        "npm ci",
        "|",
        "npm run test:e2e:crossbrowser:linux -- --workers=2",
        "|",
    ]
    assert (
        "shell: bash\n"
        "        run: |\n"
        "          mkdir -p test-results\n"
        "          npm run test:e2e:pwa:linux 2>&1 | tee test-results/pwa-run.log"
    ) in workflow
    assert workflow.index("name: pwa-workflows") < workflow.index(
        "name: Verify workflows in Chromium, Firefox and WebKit"
    )
    assert "docker build -t robopark-bot:verify apps/bot" in workflow
    assert "docker run --rm --entrypoint python robopark-bot:verify" in workflow
    assert "uv pip install" not in workflow


def test_verification_script_is_an_executable_posix_entrypoint():
    text = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\nset -eu\n")
    assert VERIFY_SCRIPT.stat().st_mode & stat.S_IXUSR


def test_verification_script_api_target_runs_only_frozen_api_commands(tmp_path: Path):
    result, commands = _run_verify(tmp_path, "api")
    assert result.returncode == 0, result.stderr
    assert commands == [
        "uv\tsync\t--frozen\t--extra\tdev",
        "uv\trun\t--frozen\t--extra\tdev\truff\tcheck\t.",
        "uv\trun\t--frozen\t--extra\tdev\truff\tformat\t--check\t.",
        "uv\trun\t--frozen\t--extra\tdev\tpython\t-m\tpytest\t-p\tno:cacheprovider\t-q\t-m\tnot load",
    ]


def test_verification_script_web_target_runs_only_web_commands(tmp_path: Path):
    result, commands = _run_verify(tmp_path, "web")
    assert result.returncode == 0, result.stderr
    assert commands == [
        "npm\tci",
        "npm\trun\tlint",
        "npm\trun\tbuild",
        "npm\ttest",
        "npm\trun\ttest:scripts",
        "npm\trun\tcheck-nav",
    ]


def test_verification_script_default_runs_all_targets_in_order(tmp_path: Path):
    result, commands = _run_verify(tmp_path)
    assert result.returncode == 0, result.stderr
    _, host_commands = _run_verify(tmp_path / "host", "host")
    assert (
        commands
        == [
            "uv\tsync\t--frozen\t--extra\tdev",
            "uv\trun\t--frozen\t--extra\tdev\truff\tcheck\t.",
            "uv\trun\t--frozen\t--extra\tdev\truff\tformat\t--check\t.",
            "uv\trun\t--frozen\t--extra\tdev\tpython\t-m\tpytest\t-p\tno:cacheprovider\t-q\t-m\tnot load",
            "uv\tsync\t--frozen\t--extra\tdev",
            "uv\trun\t--frozen\t--extra\tdev\tpython\t-m\tpytest\t-p\tno:cacheprovider\t-q\ttests/postgres",
            "uv\trun\t--project\tapps/api\t--frozen\t--extra\tdev\t--with-requirements\tapps/bot/requirements.lock\tpython\t-m\tpytest\t-p\tno:cacheprovider\t-q\ttests/bot/test_native_reports.py\ttests/bot/test_native_campaigns.py\ttests/bot/test_native_qr.py\ttests/bot/test_native_service.py\ttests/bot/test_native_transport.py\ttests/bot/test_bot_runtime_packaging.py",
            "npm\tci",
            "npm\trun\tlint",
            "npm\trun\tbuild",
            "npm\ttest",
            "npm\trun\ttest:scripts",
            "npm\trun\tcheck-nav",
            "sh\t-n\tdeploy/ops-agent.sh",
            "docker\tHOST_ENV_FILE=./host.env.example\tSAFE_PLACEHOLDERS_PRESENT=1\tcompose\t--project-name\trobopark\t-f\tdeploy/docker-compose.yml\tconfig\t--quiet",
            "docker\tHOST_ENV_FILE=\tbuild\t-t\trobopark-api:verify\tapps/api",
            "docker\tHOST_ENV_FILE=\tbuild\t-t\trobopark-web:verify\tapps/web",
            "docker\tHOST_ENV_FILE=\trun\t--rm\t--entrypoint\tpython\trobopark-api:verify\t-c\t"
            "import importlib.util, multipart, robopark_api; "
            "assert importlib.util.find_spec('pytest') is None",
        ]
        + host_commands
    )


def test_verification_script_rejects_invalid_or_excess_arguments(tmp_path: Path):
    for index, args in enumerate((("invalid",), ("api", "extra"))):
        result, commands = _run_verify(tmp_path / str(index), *args)
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith(
            f"usage: {VERIFY_SCRIPT} "
            "[fast|full|load|soak|api|api-postgres|web|bot|docker|host|ota|terminal-linux]\n"
        )
        assert commands == []


def test_verification_script_missing_docker_is_explicit(tmp_path: Path):
    result, commands = _run_verify(tmp_path, "docker", include_docker=False)
    assert result.returncode == 127
    assert result.stdout == ""
    assert result.stderr == "docker is required for the docker verification target\n"
    assert commands == []


def test_verification_script_docker_target_runs_only_docker_commands(tmp_path: Path):
    result, commands = _run_verify(tmp_path, "docker")
    assert result.returncode == 0, result.stderr
    assert commands == [
        "sh\t-n\tdeploy/ops-agent.sh",
        "docker\tHOST_ENV_FILE=./host.env.example\tSAFE_PLACEHOLDERS_PRESENT=1\tcompose\t--project-name\trobopark\t-f\tdeploy/docker-compose.yml\tconfig\t--quiet",
        "docker\tHOST_ENV_FILE=\tbuild\t-t\trobopark-api:verify\tapps/api",
        "docker\tHOST_ENV_FILE=\tbuild\t-t\trobopark-web:verify\tapps/web",
        "docker\tHOST_ENV_FILE=\trun\t--rm\t--entrypoint\tpython\trobopark-api:verify\t-c\t"
        "import importlib.util, multipart, robopark_api; "
        "assert importlib.util.find_spec('pytest') is None",
    ]


def test_root_gitignore_tracks_api_lockfile():
    ignored_lines = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "uv.lock" not in ignored_lines


def test_api_data_gitignore_allows_only_the_tracked_seed_file(tmp_path: Path):
    # Exercise the shipped ignore policy without requiring the payload to be a
    # Git checkout (release images intentionally contain no .git directory).
    (tmp_path / ".gitignore").write_bytes((REPO_ROOT / ".gitignore").read_bytes())
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    runtime = subprocess.run(
        [
            "git",
            "check-ignore",
            "--no-index",
            "--quiet",
            "--",
            "apps/api/data/runtime-cache/session.bin",
        ],
        cwd=tmp_path,
        check=False,
    )
    seed = subprocess.run(
        [
            "git",
            "check-ignore",
            "--no-index",
            "--quiet",
            "--",
            "apps/api/data/emergency_sections.json",
        ],
        cwd=tmp_path,
        check=False,
    )

    assert runtime.returncode == 0
    assert seed.returncode == 1


def test_verification_host_gate_runs_tests_without_privileged_commands(tmp_path):
    result, commands = _run_verify(tmp_path, "host")
    assert result.returncode == 0, result.stderr
    assert any("pytest" in command and "tests/host" in command for command in commands)
    assert any("check-release-migrations.py" in command for command in commands)
    assert any("generate-release-notes.py" in command for command in commands)
    assert all(command.startswith(("sh\t-n\t", "uv\t")) for command in commands)
