import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from robopark_host.storage_layout import (
    LAYOUT_PATH,
    STORAGE_ROOT,
    TARGETS,
    StorageError,
    inspect_storage,
    load_layout,
    parse_mountinfo,
    require_storage,
)

NVME_UUID = "11111111-2222-3333-4444-555555555555"
ROOT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _write_layout(root: Path, **changes) -> Path:
    value = {
        "schema": 1,
        "mode": "emmc-nvme-data",
        "uuid": NVME_UUID,
        "root_uuid": ROOT_UUID,
        "device": "/dev/nvme0n1p1",
        "state": "ready",
        "min_free_bytes": 67_108_864,
    }
    value.update(changes)
    path = root / LAYOUT_PATH.lstrip("/")
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def _device(root: Path, name: str, uuid: str) -> None:
    dev = root / "dev" / name
    dev.parent.mkdir(parents=True, exist_ok=True)
    dev.touch()
    by_uuid = root / "dev/disk/by-uuid"
    by_uuid.mkdir(parents=True, exist_ok=True)
    link = by_uuid / uuid
    if not link.exists() and not link.is_symlink():
        link.symlink_to(Path("../..") / name)


def _mount_line(
    mount_id,
    parent,
    devno,
    fsroot,
    target,
    source,
    options="rw,relatime",
    fstype="ext4",
):
    return (
        f"{mount_id} {parent} {devno} {fsroot} {target} {options} "
        f"- {fstype} {source} {options}\n"
    )


def _write_root_mounts(root: Path, *, read_only=False, fstype="ext4") -> None:
    _device(root, "nvme0n1p1", NVME_UUID)
    mountinfo = root / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True, exist_ok=True)
    options = "ro,relatime" if read_only else "rw,relatime"
    mountinfo.write_text(
        _mount_line(
            36,
            25,
            "259:2",
            "/",
            "/",
            "/dev/nvme0n1p1",
            options,
            fstype,
        ),
        encoding="utf-8",
    )


def _write_split_mounts(
    root: Path,
    *,
    omit: str | None = None,
    wrong_fsroot: str | None = None,
    read_only: str | None = None,
    storage_fsroot: str = "/",
    storage_fstype: str = "ext4",
) -> None:
    _device(root, "mmcblk0p1", ROOT_UUID)
    _device(root, "nvme0n1p1", NVME_UUID)
    lines = [_mount_line(20, 1, "179:1", "/", "/", "/dev/mmcblk0p1")]
    lines.append(
        _mount_line(
            30,
            20,
            "259:2",
            storage_fsroot,
            STORAGE_ROOT,
            "/dev/nvme0n1p1",
            "ro,relatime" if read_only == STORAGE_ROOT else "rw,relatime",
            storage_fstype,
        )
    )
    for index, (source_name, target) in enumerate(TARGETS.items(), start=31):
        if target == omit:
            continue
        fsroot = "/wrong" if target == wrong_fsroot else f"/{source_name}"
        lines.append(
            _mount_line(
                index,
                30,
                "259:2",
                fsroot,
                target,
                "/dev/nvme0n1p1",
                "ro,relatime" if target == read_only else "rw,relatime",
            )
        )
        (root / target.lstrip("/")).mkdir(parents=True, exist_ok=True)
    (root / STORAGE_ROOT.lstrip("/")).mkdir(parents=True, exist_ok=True)
    mountinfo = root / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True, exist_ok=True)
    mountinfo.write_text("".join(lines), encoding="utf-8")


def _assert_code(code: str, call) -> None:
    with pytest.raises(StorageError) as raised:
        call()
    assert raised.value.code == code
    assert str(raised.value) == code


def test_load_layout_returns_none_for_legacy_host(tmp_path):
    assert load_layout(tmp_path) is None
    assert require_storage(tmp_path) == {"state": "unmanaged"}


def test_load_layout_accepts_only_the_locked_schema(tmp_path):
    _write_layout(tmp_path)
    assert load_layout(tmp_path) == {
        "schema": 1,
        "mode": "emmc-nvme-data",
        "uuid": NVME_UUID,
        "root_uuid": ROOT_UUID,
        "device": "/dev/nvme0n1p1",
        "state": "ready",
        "min_free_bytes": 67_108_864,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"schema": 1.0},
        {"mode": "automatic"},
        {"uuid": ""},
        {"root_uuid": "../../secret"},
        {"device": "/dev/sda1"},
        {"state": "failed"},
        {"min_free_bytes": True},
        {"min_free_bytes": 0},
        {"min_free_bytes": -1},
        {"extra": "field"},
    ],
)
def test_load_layout_rejects_malformed_or_extended_schema(tmp_path, change):
    _write_layout(tmp_path, **change)
    _assert_code("layout_invalid", lambda: load_layout(tmp_path))


def test_load_layout_rejects_duplicate_keys_and_oversized_input(tmp_path):
    path = _write_layout(tmp_path)
    path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
    _assert_code("layout_invalid", lambda: load_layout(tmp_path))
    path.write_bytes(b" " * 16_385)
    _assert_code("layout_invalid", lambda: load_layout(tmp_path))


def test_load_layout_rejects_symlink_and_writable_manifest(tmp_path):
    path = _write_layout(tmp_path)
    real = path.with_name("real.json")
    path.rename(real)
    path.symlink_to(real.name)
    _assert_code("layout_unsafe", lambda: load_layout(tmp_path))
    path.unlink()
    real.rename(path)
    path.chmod(0o666)
    _assert_code("layout_unsafe", lambda: load_layout(tmp_path))


def test_load_layout_requires_exact_private_mode_and_single_link(tmp_path):
    path = _write_layout(tmp_path)
    path.chmod(0o644)
    _assert_code("layout_unsafe", lambda: load_layout(tmp_path))
    path.chmod(0o600)
    os.link(path, path.with_name("layout-hardlink.json"))
    _assert_code("layout_unsafe", lambda: load_layout(tmp_path))


def test_parse_mountinfo_decodes_paths_and_rejects_unbounded_input():
    records = parse_mountinfo(
        "36 25 259:2 /application\\040data /opt/robopark\\040app rw - ext4 /dev/nvme0n1p1 rw\n"
    )
    assert records == [
        {
            "target": "/opt/robopark app",
            "fsroot": "/application data",
            "source": "/dev/nvme0n1p1",
            "fstype": "ext4",
            "options": ["rw"],
            "major_minor": "259:2",
            "uuid": None,
            "device_type": None,
        }
    ]
    _assert_code(
        "storage_probe_failed", lambda: parse_mountinfo("x" * (1024 * 1024 + 1))
    )


def test_inspect_storage_reports_uuid_and_device_lineage_from_fake_root(tmp_path):
    _write_split_mounts(tmp_path)
    result = inspect_storage(tmp_path)
    assert result["root_mount"]["uuid"] == ROOT_UUID
    assert result["root_mount"]["device_type"] == "mmc"
    storage = next(row for row in result["mounts"] if row["target"] == STORAGE_ROOT)
    assert storage["uuid"] == NVME_UUID
    assert storage["device_type"] == "nvme"
    assert result["block_devices"] == [
        {
            "name": "mmcblk0p1",
            "path": "/dev/mmcblk0p1",
            "type": "part",
            "pkname": "mmcblk0",
            "uuid": ROOT_UUID,
            "fstype": None,
            "size": None,
            "ro": False,
            "mountpoints": ["/"],
        },
        {
            "name": "nvme0n1p1",
            "path": "/dev/nvme0n1p1",
            "type": "part",
            "pkname": "nvme0n1",
            "uuid": NVME_UUID,
            "fstype": None,
            "size": None,
            "ro": False,
            "mountpoints": [
                STORAGE_ROOT,
                *TARGETS.values(),
            ],
        },
    ]


def test_inspect_storage_normalizes_bounded_findmnt_and_lsblk_output(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "lsblk":
            stdout = json.dumps(
                {
                    "blockdevices": [
                        {
                            "name": "nvme0n1",
                            "path": "/dev/nvme0n1",
                            "type": "disk",
                            "pkname": None,
                            "uuid": None,
                            "fstype": None,
                            "size": 1_000_000,
                            "ro": False,
                            "mountpoints": [],
                            "children": [
                                {
                                    "name": "nvme0n1p1",
                                    "path": "/dev/nvme0n1p1",
                                    "type": "part",
                                    "pkname": "nvme0n1",
                                    "uuid": NVME_UUID,
                                    "fstype": "ext4",
                                    "size": 900_000,
                                    "ro": 0,
                                    "mountpoints": ["/"],
                                }
                            ],
                        }
                    ],
                }
            )
        else:
            stdout = json.dumps(
                {
                    "filesystems": [
                        {
                            "target": "/",
                            "source": "/dev/nvme0n1p1",
                            "fstype": "ext4",
                            "options": "rw,relatime",
                            "fsroot": "/",
                            "uuid": None,
                            "maj:min": "259:1",
                        }
                    ]
                }
            )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    result = inspect_storage(Path("/"))
    assert result["root_mount"] == {
        "target": "/",
        "source": "/dev/nvme0n1p1",
        "fstype": "ext4",
        "options": ["relatime", "rw"],
        "fsroot": "/",
        "uuid": NVME_UUID,
        "major_minor": "259:1",
        "device_type": "nvme",
    }
    assert [row["path"] for row in result["block_devices"]] == [
        "/dev/nvme0n1",
        "/dev/nvme0n1p1",
    ]
    assert len(calls) == 2
    # ProtectSystem=strict deliberately remounts / read-only inside services.
    # Validate PID 1's host topology rather than mistaking that for disk failure.
    assert calls[1][0][1:3] == ["--task", "1"]
    assert all(
        call[1]["timeout"] == 5 and call[1]["stdin"] is subprocess.DEVNULL
        for call in calls
    )


def test_inspect_storage_keeps_unknown_uuid_visible_instead_of_failing(tmp_path):
    device = tmp_path / "dev/sda1"
    device.parent.mkdir(parents=True)
    device.touch()
    mountinfo = tmp_path / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True)
    mountinfo.write_text(
        _mount_line(20, 1, "8:1", "/", "/", "/dev/sda1"),
        encoding="utf-8",
    )
    result = inspect_storage(tmp_path)
    assert result["root_mount"]["uuid"] is None
    assert result["block_devices"][0]["path"] == "/dev/sda1"


def test_require_storage_accepts_nvme_root_and_all_canonical_paths(tmp_path):
    _write_layout(tmp_path, mode="nvme-root", root_uuid=NVME_UUID)
    _write_root_mounts(tmp_path)
    result = require_storage(tmp_path)
    assert result["state"] == "ready"
    assert result["mode"] == "nvme-root"
    assert set(result["targets"]) == set(TARGETS.values())


def test_require_storage_rejects_wrong_root_uuid_and_read_only_root(tmp_path):
    _write_layout(tmp_path, mode="nvme-root", root_uuid=ROOT_UUID)
    _write_root_mounts(tmp_path)
    _assert_code("storage_root_uuid_mismatch", lambda: require_storage(tmp_path))
    _write_layout(tmp_path, mode="nvme-root", root_uuid=NVME_UUID)
    _write_root_mounts(tmp_path, read_only=True)
    _assert_code("storage_read_only", lambda: require_storage(tmp_path))
    _write_root_mounts(tmp_path, fstype="xfs")
    _assert_code("storage_source_mismatch", lambda: require_storage(tmp_path))


def test_require_storage_accepts_exact_split_bind_mounts(tmp_path):
    _write_layout(tmp_path)
    _write_split_mounts(tmp_path)
    result = require_storage(tmp_path)
    assert result["state"] == "ready"
    assert result["mode"] == "emmc-nvme-data"
    assert result["device"] == "/dev/nvme0n1p1"


def test_require_storage_rejects_missing_or_wrong_bind_subtree(tmp_path):
    _write_layout(tmp_path)
    _write_split_mounts(tmp_path, omit="/var/lib/docker")
    _assert_code("storage_mount_missing", lambda: require_storage(tmp_path))
    _write_split_mounts(tmp_path, wrong_fsroot="/var/lib/docker")
    _assert_code("storage_source_mismatch", lambda: require_storage(tmp_path))


def test_require_storage_rejects_subtree_as_base_mount_and_non_ext4_nvme(tmp_path):
    _write_layout(tmp_path)
    _write_split_mounts(tmp_path, storage_fsroot="/application")
    _assert_code("storage_source_mismatch", lambda: require_storage(tmp_path))
    _write_split_mounts(tmp_path, storage_fstype="xfs")
    _assert_code("storage_source_mismatch", lambda: require_storage(tmp_path))


def test_require_storage_rejects_preparing_unless_explicitly_allowed(tmp_path):
    _write_layout(tmp_path, state="preparing")
    _write_split_mounts(tmp_path)
    _assert_code("storage_preparing", lambda: require_storage(tmp_path))
    assert require_storage(tmp_path, allow_preparing=True)["state"] == "preparing"


def test_require_storage_can_require_a_manifest(tmp_path):
    _assert_code(
        "layout_missing",
        lambda: require_storage(tmp_path, require_layout=True),
    )


def test_require_storage_checks_free_space_without_recursive_accounting(
    tmp_path, monkeypatch
):
    _write_layout(tmp_path)
    _write_split_mounts(tmp_path)
    values = os.statvfs(tmp_path)

    class LowSpace:
        f_bavail = 1
        f_frsize = 1

    monkeypatch.setattr(os, "statvfs", lambda _path: LowSpace())
    _assert_code("storage_space_low", lambda: require_storage(tmp_path))
    assert require_storage(tmp_path, check_space=False)["state"] == "ready"
    assert values.f_frsize > 0


def test_standalone_check_isolated_python_accepts_legacy_and_reports_safe_error(
    tmp_path,
):
    module = Path(__file__).parents[2] / "deploy/host/robopark_host/storage_layout.py"
    env = {**os.environ, "ROBOPARK_TESTING": "1"}
    legacy = subprocess.run(
        [sys.executable, "-I", str(module), "check", "--root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=5,
    )
    assert legacy.returncode == 0
    assert json.loads(legacy.stdout) == {"state": "unmanaged"}
    _write_layout(tmp_path, state="preparing")
    unsafe = subprocess.run(
        [sys.executable, "-I", str(module), "check", "--root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=5,
    )
    assert unsafe.returncode == 2
    assert json.loads(unsafe.stdout) == {"error": "storage_preparing", "safe": False}


@pytest.mark.parametrize(
    "source,fsroot",
    [("/dev/nvme1n1p1", "/var/lib/docker"), ("/dev/nvme0n1p1", "/other")],
)
def test_nvme_root_rejects_cloned_uuid_or_redirected_work_directory(
    tmp_path, monkeypatch, source, fsroot
):
    from robopark_host import storage_layout

    _write_layout(tmp_path, mode="nvme-root", root_uuid=NVME_UUID)
    _write_root_mounts(tmp_path)
    inventory = inspect_storage(tmp_path)
    inventory["mounts"].append(
        {
            **inventory["root_mount"],
            "source": source,
            "target": "/var/lib/docker",
            "fsroot": fsroot,
        }
    )
    monkeypatch.setattr(storage_layout, "inspect_storage", lambda _root: inventory)
    _assert_code("storage_path_mismatch", lambda: require_storage(tmp_path))


def test_unknown_mount_access_is_not_treated_as_writable(tmp_path, monkeypatch):
    from robopark_host import storage_layout

    _write_layout(tmp_path, mode="nvme-root", root_uuid=NVME_UUID)
    _write_root_mounts(tmp_path)
    inventory = inspect_storage(tmp_path)
    inventory["root_mount"]["options"] = []
    monkeypatch.setattr(storage_layout, "inspect_storage", lambda _root: inventory)
    _assert_code("storage_read_only", lambda: require_storage(tmp_path))
