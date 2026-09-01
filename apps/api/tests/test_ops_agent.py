from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
OPS_AGENT = REPO_ROOT / "deploy" / "ops-agent.sh"


@dataclass(frozen=True)
class AgentTree:
    root: Path
    ops_root: Path
    staging: Path
    source: Path
    host_repo: Path
    bin_dir: Path
    job_flag: Path
    result_file: Path
    docker_log: Path

    def environment(self, *, copy_mode: str = "copy", docker_exit: int = 0) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join((str(self.bin_dir), os.defpath)),
            "OPS_ROOT": str(self.ops_root),
            "HOST_REPO": str(self.host_repo),
            "HOST_ENV_FILE": "",
            "READY_TIMEOUT_SECONDS": "180",
            "OPS_AGENT_COPY_MODE": copy_mode,
            "FAKE_DOCKER_EXIT": str(docker_exit),
            "FAKE_DOCKER_LOG": str(self.docker_log),
        }


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _agent_tree(tmp_path: Path) -> AgentTree:
    ops_root = tmp_path / "ops"
    staging = ops_root / "staging"
    source = staging / "release"
    source.mkdir(parents=True)

    host_repo = tmp_path / "host"
    (host_repo / "deploy").mkdir(parents=True)
    (host_repo / "deploy" / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    _write_executable(
        bin_dir / "docker",
        """
printf 'HOST_ENV_FILE=%s\\n' "${HOST_ENV_FILE:-}" > "$FAKE_DOCKER_LOG"
printf '%s\\n' "$@" >> "$FAKE_DOCKER_LOG"
exit "$FAKE_DOCKER_EXIT"
""".strip(),
    )
    # `once` must never sleep. This makes the pre-change infinite loop fail quickly.
    _write_executable(bin_dir / "sleep", "exit 99")

    job_flag = ops_root / "rebuild.requested"
    job_flag.write_text(f"job-1\n{source}\n", encoding="utf-8")
    return AgentTree(
        root=tmp_path,
        ops_root=ops_root,
        staging=staging,
        source=source,
        host_repo=host_repo,
        bin_dir=bin_dir,
        job_flag=job_flag,
        result_file=ops_root / "rebuild.result",
        docker_log=docker_log,
    )


def _run_agent(tree: AgentTree, *, copy_mode: str = "copy", docker_exit: int = 0):
    return subprocess.run(
        ["sh", str(OPS_AGENT), "once"],
        env=tree.environment(copy_mode=copy_mode, docker_exit=docker_exit),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _result(tree: AgentTree) -> dict[str, object]:
    return json.loads(tree.result_file.read_text(encoding="utf-8"))


def test_copy_fallback_preserves_live_env_and_updates_regular_file(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_host_env = tree.host_repo / "deploy" / "host.env"
    live_nested_env = tree.host_repo / "config" / ".env.production"
    live_nested_env.parent.mkdir()
    live_host_env.write_bytes(b"LIVE_HOST_ENV_SENTINEL\n")
    live_nested_env.write_bytes(b"LIVE_NESTED_ENV_SENTINEL\n")
    (tree.host_repo / "ordinary.txt").write_text("old\n", encoding="utf-8")

    staged_host_env = tree.source / "deploy" / "host.env"
    staged_nested_env = tree.source / "config" / ".env.production"
    staged_host_env.parent.mkdir()
    staged_nested_env.parent.mkdir()
    staged_host_env.write_bytes(b"STAGED_HOST_ENV_SENTINEL\n")
    staged_nested_env.write_bytes(b"STAGED_NESTED_ENV_SENTINEL\n")
    staged_env_symlink = tree.source / "config" / ".env.symlink"
    staged_env_symlink.symlink_to("../ordinary.txt")
    (tree.source / "ordinary.txt").write_text("new\n", encoding="utf-8")

    completed = _run_agent(tree, copy_mode="copy")

    assert completed.returncode == 0, completed.stderr
    assert live_host_env.read_bytes() == b"LIVE_HOST_ENV_SENTINEL\n"
    assert live_nested_env.read_bytes() == b"LIVE_NESTED_ENV_SENTINEL\n"
    assert not staged_host_env.exists()
    assert not staged_nested_env.exists()
    assert not staged_env_symlink.exists()
    assert not staged_env_symlink.is_symlink()
    assert (tree.host_repo / "ordinary.txt").read_text(encoding="utf-8") == "new\n"
    assert not tree.job_flag.exists()


def test_success_waits_for_ready_services(tmp_path: Path):
    tree = _agent_tree(tmp_path)

    completed = _run_agent(tree)

    assert completed.returncode == 0, completed.stderr
    docker_log = tree.docker_log.read_text(encoding="utf-8").splitlines()
    assert docker_log[0] == f"HOST_ENV_FILE={tree.host_repo / 'deploy' / 'host.env'}"
    assert docker_log[1:] == [
        "compose",
        "-f",
        str(tree.host_repo / "deploy" / "docker-compose.yml"),
        "up",
        "-d",
        "--build",
        "--wait",
        "--wait-timeout",
        "180",
        "api",
        "web",
    ]
    assert _result(tree) == {"job_id": "job-1", "ok": True}
    assert not tree.job_flag.exists()


def test_compose_failure_is_reported(tmp_path: Path):
    tree = _agent_tree(tmp_path)

    completed = _run_agent(tree, docker_exit=17)

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "compose_failed"}
    assert not tree.job_flag.exists()


@pytest.mark.parametrize("source_case", ["outside", "traversal", "symlink"])
def test_unsafe_source_is_rejected_without_touching_host_repo(
    tmp_path: Path, source_case: str
):
    tree = _agent_tree(tmp_path)
    outside = tree.ops_root / "outside"
    outside.mkdir()
    (outside / ".env.production").write_bytes(b"OUTSIDE_ENV_SENTINEL\n")
    (outside / "ordinary.txt").write_text("outside-new\n", encoding="utf-8")

    if source_case == "outside":
        source = tmp_path / "outside-staging"
        source.mkdir()
        (source / ".env.production").write_bytes(b"OUTSIDE_ENV_SENTINEL\n")
        (source / "ordinary.txt").write_text("outside-new\n", encoding="utf-8")
    elif source_case == "traversal":
        source = tree.staging / ".." / "outside"
    else:
        source = tree.staging / "escape"
        source.symlink_to(outside, target_is_directory=True)

    live_env = tree.host_repo / "deploy" / "host.env"
    live_file = tree.host_repo / "ordinary.txt"
    live_env.write_bytes(b"LIVE_ENV_SENTINEL\n")
    live_file.write_text("live-old\n", encoding="utf-8")
    tree.job_flag.write_text(f"job-1\n{source}\n", encoding="utf-8")

    completed = _run_agent(tree)

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "unsafe_src"}
    assert live_env.read_bytes() == b"LIVE_ENV_SENTINEL\n"
    assert live_file.read_text(encoding="utf-8") == "live-old\n"
    assert (outside / ".env.production").read_bytes() == b"OUTSIDE_ENV_SENTINEL\n"
    assert not tree.docker_log.exists()
    assert not tree.job_flag.exists()


def test_missing_staging_is_reported_without_touching_host_repo(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_file = tree.host_repo / "ordinary.txt"
    live_file.write_text("live-old\n", encoding="utf-8")
    missing = tree.staging / "missing"
    tree.job_flag.write_text(f"job-1\n{missing}\n", encoding="utf-8")

    completed = _run_agent(tree)

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "staging_missing"}
    assert live_file.read_text(encoding="utf-8") == "live-old\n"
    assert not tree.docker_log.exists()
    assert not tree.job_flag.exists()


def test_invalid_copy_mode_is_reported_without_copying(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_file = tree.host_repo / "ordinary.txt"
    live_file.write_text("live-old\n", encoding="utf-8")
    (tree.source / "ordinary.txt").write_text("staged-new\n", encoding="utf-8")

    completed = _run_agent(tree, copy_mode="invalid")

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "invalid_copy_mode"}
    assert live_file.read_text(encoding="utf-8") == "live-old\n"
    assert not tree.docker_log.exists()
    assert not tree.job_flag.exists()


def test_sanitize_failure_is_reported_without_touching_host_repo(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_file = tree.host_repo / "ordinary.txt"
    live_file.write_text("live-old\n", encoding="utf-8")
    staged_env = tree.source / ".env.production"
    staged_env.write_bytes(b"STAGED_ENV_SENTINEL\n")
    _write_executable(tree.bin_dir / "find", "exit 7")

    completed = _run_agent(tree)

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "sanitize_failed"}
    assert live_file.read_text(encoding="utf-8") == "live-old\n"
    assert staged_env.read_bytes() == b"STAGED_ENV_SENTINEL\n"
    assert not tree.docker_log.exists()
    assert not tree.job_flag.exists()


def test_secret_removal_failure_is_reported_without_touching_host_repo(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_file = tree.host_repo / "ordinary.txt"
    live_file.write_text("live-old\n", encoding="utf-8")
    staged_env = tree.source / ".env.production"
    staged_env.write_bytes(b"STAGED_ENV_SENTINEL\n")
    _write_executable(
        tree.bin_dir / "rm",
        """
last=
for argument in "$@"; do
    last="$argument"
done
case "$last" in
    *.env.production) exit 7 ;;
esac
exec /bin/rm "$@"
""".strip(),
    )

    completed = _run_agent(tree)

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "sanitize_failed"}
    assert live_file.read_text(encoding="utf-8") == "live-old\n"
    assert staged_env.read_bytes() == b"STAGED_ENV_SENTINEL\n"
    assert not tree.docker_log.exists()
    assert not tree.job_flag.exists()


def test_copy_failure_is_reported_without_touching_host_repo(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_file = tree.host_repo / "ordinary.txt"
    live_file.write_text("live-old\n", encoding="utf-8")
    (tree.source / "ordinary.txt").write_text("staged-new\n", encoding="utf-8")
    _write_executable(tree.bin_dir / "cp", "exit 8")

    completed = _run_agent(tree)

    assert completed.returncode != 0
    assert _result(tree) == {"job_id": "job-1", "ok": False, "error": "copy_failed"}
    assert live_file.read_text(encoding="utf-8") == "live-old\n"
    assert not tree.docker_log.exists()
    assert not tree.job_flag.exists()


def test_auto_mode_uses_available_rsync_without_workstation_dependency(tmp_path: Path):
    tree = _agent_tree(tmp_path)
    live_file = tree.host_repo / "ordinary.txt"
    live_file.write_text("live-old\n", encoding="utf-8")
    (tree.source / "ordinary.txt").write_text("staged-new\n", encoding="utf-8")
    _write_executable(
        tree.bin_dir / "rsync",
        """
printf '%s\\n' "$@" > "${FAKE_DOCKER_LOG}.rsync"
previous=
last=
for argument in "$@"; do
    previous="$last"
    last="$argument"
done
/bin/cp -a "$previous"/. "$last"/.
""".strip(),
    )

    completed = _run_agent(tree, copy_mode="auto")

    assert completed.returncode == 0, completed.stderr
    assert Path(f"{tree.docker_log}.rsync").is_file()
    assert live_file.read_text(encoding="utf-8") == "staged-new\n"
    assert _result(tree) == {"job_id": "job-1", "ok": True}
    assert not tree.job_flag.exists()
