"""Storage preparation never formats disks or hides an existing installation."""

import json

import pytest

UUID = "11111111-2222-3333-4444-555555555555"
ROOT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def inventory(mode="emmc-nvme-data"):
    root_source = "/dev/mmcblk0p1" if mode == "emmc-nvme-data" else "/dev/nvme0n1p1"
    root_uuid = ROOT_UUID if mode == "emmc-nvme-data" else UUID
    root_mount = {
        "target": "/",
        "source": root_source,
        "uuid": root_uuid,
        "fstype": "ext4",
        "options": "rw,relatime",
        "fsroot": "/",
        "device_type": "part",
        "major_minor": "179:1",
    }
    return {
        "root_mount": root_mount,
        "mounts": [root_mount],
        "block_devices": [
            {
                "name": "mmcblk0p1",
                "path": "/dev/mmcblk0p1",
                "type": "part",
                "pkname": "mmcblk0",
                "uuid": ROOT_UUID,
                "fstype": "ext4",
                "size": 64 * 1024**3,
                "ro": False,
                "mountpoints": ["/"],
            },
            {
                "name": "nvme0n1p1",
                "path": "/dev/nvme0n1p1",
                "type": "part",
                "pkname": "nvme0n1",
                "uuid": UUID,
                "fstype": "ext4",
                "size": 1000 * 1000**3,
                "ro": False,
                "mountpoints": [] if mode == "emmc-nvme-data" else ["/"],
            },
        ],
    }


@pytest.fixture
def setup_host(tmp_path, monkeypatch):
    from robopark_host import storage_setup as setup

    (tmp_path / "etc").mkdir()
    (tmp_path / "proc").mkdir()
    monkeypatch.setattr(setup, "inspect_storage", lambda root: inventory())
    calls = []

    def run(argv, *, root):
        calls.append(argv)
        if argv[:2] == ["systemctl", "show"]:
            return "inactive\n"
        return ""

    monkeypatch.setattr(setup, "_run", run)
    return setup, tmp_path, calls


def test_plan_is_read_only_and_lists_both_container_roots(setup_host):
    setup, root, calls = setup_host
    plan = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)
    assert plan["layout"]["uuid"] == UUID
    assert plan["layout"]["root_uuid"] == ROOT_UUID
    assert {row["target"] for row in plan["mounts"]} >= {
        "/var/lib/docker",
        "/var/lib/containerd",
    }
    assert not (root / "etc/robopark-storage").exists()
    assert not any(
        argv[0] in {"mount", "umount", "mkfs.ext4", "systemctl"} for argv in calls
    )


def test_root_mode_requires_running_root_on_selected_nvme(setup_host, monkeypatch):
    setup, root, _ = setup_host
    with pytest.raises(RuntimeError, match="storage_root_not_nvme"):
        setup.plan_storage("nvme-root", "/dev/nvme0n1p1", root=root)
    monkeypatch.setattr(setup, "inspect_storage", lambda root: inventory("nvme-root"))
    plan = setup.plan_storage("nvme-root", None, root=root)
    assert plan["layout"]["uuid"] == plan["layout"]["root_uuid"] == UUID
    assert plan["mounts"] == []


@pytest.mark.parametrize("path", ["opt/robopark", "var/lib/robopark", "var/lib/docker"])
def test_plan_rejects_nonempty_destinations_without_mutation(setup_host, path):
    setup, root, calls = setup_host
    directory = root / path
    directory.mkdir(parents=True)
    (directory / "existing-data").write_text("keep")
    with pytest.raises(RuntimeError, match="storage_target_not_empty"):
        setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)
    assert (directory / "existing-data").read_text() == "keep"
    assert calls == []


def test_plan_rejects_mounted_nvme_elsewhere(setup_host, monkeypatch):
    setup, root, _ = setup_host
    data = inventory()
    data["block_devices"][1]["mountpoints"] = ["/media/my-data"]
    monkeypatch.setattr(setup, "inspect_storage", lambda root: data)
    with pytest.raises(RuntimeError, match="storage_device_in_use"):
        setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)


def test_prepare_confirmation_is_bound_to_disk_uuid(setup_host):
    setup, root, calls = setup_host
    with pytest.raises(RuntimeError, match="storage_confirmation_required"):
        setup.prepare_storage(
            "emmc-nvme-data", "/dev/nvme0n1p1", confirmation="yes", root=root
        )
    assert not (root / "etc/robopark-storage").exists()
    assert not any(argv[0] == "mount" for argv in calls)


def test_generated_mounts_do_not_require_local_fs_boot(setup_host):
    setup, root, _ = setup_host
    plan = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)
    units = setup.storage_units(plan["layout"])
    mount = units["srv-robopark\\x2dstorage.mount"]
    assert "DefaultDependencies=no" in mount
    assert "JobTimeoutSec=20s" in mount
    assert "WantedBy=local-fs.target" not in mount
    assert "RequiredBy=local-fs.target" not in mount
    for path in (
        "docker.service.d/50-robopark-storage.conf",
        "docker.socket.d/50-robopark-storage.conf",
        "containerd.service.d/50-robopark-storage.conf",
    ):
        assert "Requires=robopark-storage.target" in units[path]
        assert "/usr/lib/robopark-storage/storage_layout.py check" in units[path]
    assert "storage_watchdog.py" in units["robopark-storage-watchdog.service"]


def test_prepare_refuses_running_daemon_before_mount_or_manifest(
    setup_host, monkeypatch
):
    setup, root, calls = setup_host
    plan = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)
    monkeypatch.setattr(setup, "_run", lambda argv, root: "active\n")
    with pytest.raises(RuntimeError, match="storage_daemon_running"):
        setup.prepare_storage(
            "emmc-nvme-data",
            "/dev/nvme0n1p1",
            confirmation=plan["confirmation"],
            root=root,
        )
    assert not (root / "etc/robopark-storage").exists()
    assert calls == []


def test_failed_prepare_keeps_preparing_manifest_and_can_resume(
    setup_host, monkeypatch
):
    setup, root, calls = setup_host
    plan = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)
    monkeypatch.setattr(setup, "_probe_empty_filesystem", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        setup,
        "_activate_layout",
        lambda *_args, **_kw: (_ for _ in ()).throw(RuntimeError("mount_failed")),
    )
    with pytest.raises(RuntimeError, match="mount_failed"):
        setup.prepare_storage(
            "emmc-nvme-data",
            "/dev/nvme0n1p1",
            confirmation=plan["confirmation"],
            root=root,
        )
    manifest = root / "etc/robopark-storage/layout.json"
    assert json.loads(manifest.read_text())["state"] == "preparing"
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert (root / "usr/lib/robopark-storage/storage_layout.py").is_file()
    monkeypatch.setattr(setup, "_activate_layout", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    result = setup.prepare_storage(
        "emmc-nvme-data", "/dev/nvme0n1p1", confirmation=plan["confirmation"], root=root
    )
    assert result["state"] == "ready"
    assert json.loads(manifest.read_text())["state"] == "ready"
    assert not any(
        argv[0].startswith("mkfs") or argv[0] in {"wipefs", "parted", "sfdisk"}
        for argv in calls
    )


def test_refresh_validates_all_guard_sources_before_replacing_any(
    setup_host, monkeypatch
):
    setup, root, _ = setup_host
    monkeypatch.setattr(setup, "load_layout", lambda _root: {"state": "ready"})
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    old = root / "usr/lib/robopark-storage/storage_layout.py"
    old.parent.mkdir(parents=True)
    old.write_text("old guard")
    release = root / "candidate"
    source = release / "deploy/host/robopark_host/storage_layout.py"
    source.parent.mkdir(parents=True)
    source.write_text("new guard")
    with pytest.raises(RuntimeError, match="storage_guard_payload_invalid"):
        setup.refresh_storage_guard(root, release)
    assert old.read_text() == "old guard"


def test_refresh_repairs_current_units_for_ready_layout(setup_host, monkeypatch):
    setup, root, _ = setup_host
    layout = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)["layout"]
    layout["state"] = "ready"
    monkeypatch.setattr(setup, "load_layout", lambda _root: layout)
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    release = root / "candidate"
    package = release / "deploy/host/robopark_host"
    package.mkdir(parents=True)
    for name in ("storage_layout.py", "storage_watchdog.py"):
        (package / name).write_text("# valid packaged guard\n")
    stale = root / "etc/systemd/system/robopark-ai.service.d/50-robopark-storage.conf"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n")

    setup.refresh_storage_guard(root, release)

    expected = setup.storage_units(layout)
    assert (
        stale.read_text() == expected["robopark-ai.service.d/50-robopark-storage.conf"]
    )
    assert (
        root
        / "etc/systemd/system/robopark-ai-broker.service.d/50-robopark-storage.conf"
    ).read_text() == expected["robopark-ai-broker.service.d/50-robopark-storage.conf"]


def test_refresh_missing_storage_fails_before_repairing_units(setup_host, monkeypatch):
    setup, root, _ = setup_host
    monkeypatch.setattr(setup, "load_layout", lambda _root: {"state": "ready"})

    def fail(*_args, **_kwargs):
        raise setup.StorageError("storage_mount_missing")

    monkeypatch.setattr(setup, "require_storage", fail)
    stale = root / "etc/systemd/system/robopark-ai.service.d/50-robopark-storage.conf"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n")

    with pytest.raises(RuntimeError, match="storage_mount_missing"):
        setup.refresh_storage_guard(root, root / "candidate")

    assert stale.read_text() == "stale\n"


def test_refresh_validates_all_unit_paths_before_replacing_guard(
    setup_host, monkeypatch
):
    setup, root, _ = setup_host
    layout = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)["layout"]
    layout["state"] = "ready"
    monkeypatch.setattr(setup, "load_layout", lambda _root: layout)
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    guard = root / "usr/lib/robopark-storage/storage_layout.py"
    guard.parent.mkdir(parents=True)
    guard.write_text("old guard\n")
    release = root / "candidate"
    package = release / "deploy/host/robopark_host"
    package.mkdir(parents=True)
    for name in ("storage_layout.py", "storage_watchdog.py"):
        (package / name).write_text("# valid packaged guard\n")
    unsafe = root / "etc/systemd/system/docker.service.d"
    unsafe.parent.mkdir(parents=True)
    unsafe.symlink_to(root / "outside-systemd")

    with pytest.raises(RuntimeError, match="storage_path_unsafe"):
        setup.refresh_storage_guard(root, release)

    assert guard.read_text() == "old guard\n"


def test_ready_prepare_retries_watchdog_activation(setup_host, monkeypatch):
    setup, root, calls = setup_host
    plan = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)
    manifest = root / "etc/robopark-storage/layout.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({**plan["layout"], "state": "ready"}))
    manifest.chmod(0o600)
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    setup.prepare_storage(
        "emmc-nvme-data", "/dev/nvme0n1p1", confirmation=plan["confirmation"], root=root
    )
    assert ["systemctl", "enable", "--now", "robopark-storage-watchdog.timer"] in calls
    expected = setup.storage_units(plan["layout"])
    assert (
        root / "etc/systemd/system/robopark-ai.service.d/50-robopark-storage.conf"
    ).read_text() == expected["robopark-ai.service.d/50-robopark-storage.conf"]
    assert ["systemctl", "daemon-reload"] in calls
    assert not any(argv[0] in {"mount", "umount"} for argv in calls)


def test_managed_container_roots_reject_live_docker_drift_without_starting_daemon(
    setup_host, monkeypatch
):
    setup, root, calls = setup_host

    def run(argv, *, root):
        calls.append(argv)
        if argv[:2] == ["systemctl", "show"] and "--property=ExecStart" in argv:
            return (
                "/usr/bin/dockerd\n"
                if argv[2] == "docker.service"
                else "/usr/bin/containerd\n"
            )
        if argv[:2] == ["systemctl", "show"]:
            return "active\n" if argv[2] == "docker.service" else "inactive\n"
        if argv == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            return "/mnt/foreign-docker\n"
        raise AssertionError(argv)

    monkeypatch.setattr(setup, "_run", run)

    with pytest.raises(RuntimeError, match="storage_custom_container_root"):
        setup.require_managed_container_roots(root, run=run)

    assert ["systemctl", "start", "docker.service"] not in calls


def test_managed_container_roots_reject_config_drift_before_live_probe(
    setup_host, monkeypatch
):
    setup, root, calls = setup_host
    config = root / "etc/docker/daemon.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"data-root": "/mnt/foreign-docker"}\n')

    with pytest.raises(RuntimeError, match="storage_custom_container_root"):
        setup.require_managed_container_roots(root)

    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"source": "/dev/nvme1n1p1"},
        {"fstype": "xfs"},
        {"device_type": "mmc"},
        {"options": ["ro"]},
    ],
)
def test_activation_rejects_wrong_mount_before_creating_work_directories(
    setup_host, monkeypatch, change
):
    setup, root, _ = setup_host
    layout = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)["layout"]
    data = inventory()
    data["mounts"].append(
        {
            "target": setup.STORAGE_ROOT,
            "source": layout["device"],
            "uuid": UUID,
            "fsroot": "/",
            "fstype": "ext4",
            "device_type": "nvme",
            "options": ["rw"],
            **change,
        }
    )
    monkeypatch.setattr(setup, "inspect_storage", lambda _root: data)
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    with pytest.raises(RuntimeError, match="storage_mount_missing"):
        setup._activate_layout(layout, root=root)
    assert not (root / "srv/robopark-storage/application").exists()


def test_guard_dependency_runs_before_vendor_prestart_and_refresh_can_write_os_copy(
    setup_host,
):
    setup, root, _ = setup_host
    units = setup.storage_units(
        setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)["layout"]
    )
    guard = units["robopark-storage-check.service"]
    assert "Type=oneshot" in guard
    assert "RemainAfterExit=yes" not in guard
    assert "check --presence-only" in guard
    for unit in ("robopark.service", "docker.service", "docker.socket"):
        dropin = units[unit + ".d/50-robopark-storage.conf"]
        assert "Requires=robopark-storage-check.service" in dropin
        assert "After=robopark-storage-check.service" in dropin
    for unit in ("robopark-commands.service", "robopark-updater.service"):
        assert (
            "ReadWritePaths=/usr/lib/robopark-storage"
            in units[unit + ".d/50-robopark-storage.conf"]
        )


def test_activation_rechecks_contents_after_ext4_journal_replay(
    setup_host, monkeypatch
):
    setup, root, _ = setup_host
    layout = setup.plan_storage("emmc-nvme-data", "/dev/nvme0n1p1", root=root)["layout"]
    data = inventory()
    data["mounts"].append(
        {
            "target": setup.STORAGE_ROOT,
            "source": layout["device"],
            "uuid": UUID,
            "fsroot": "/",
            "fstype": "ext4",
            "device_type": "nvme",
            "options": ["rw"],
        }
    )
    monkeypatch.setattr(setup, "inspect_storage", lambda _root: data)
    monkeypatch.setattr(
        setup, "require_storage", lambda *_args, **_kw: {"state": "ready"}
    )
    base = root / "srv/robopark-storage"
    base.mkdir(parents=True)
    recovered = base / "recovered-user-file"
    recovered.write_text("keep")
    with pytest.raises(RuntimeError, match="storage_device_not_empty"):
        setup._activate_layout(layout, root=root)
    assert recovered.read_text() == "keep"
    assert not (base / "application").exists()


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/dockerd --config-file=/etc/vendor-docker.json",
        "/usr/bin/containerd --config=/etc/vendor-containerd.toml",
        "/usr/bin/dockerd $DOCKER_OPTS",
    ],
)
def test_prepare_rejects_unverifiable_daemon_configuration(
    setup_host, monkeypatch, command
):
    setup, root, _ = setup_host

    def run(argv, *, root):
        return command if "--property=ExecStart" in argv else "inactive"

    monkeypatch.setattr(setup, "_run", run)
    with pytest.raises(RuntimeError, match="storage_custom_container_root"):
        setup._require_stopped(root)
