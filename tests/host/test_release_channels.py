from test_github_releases import config

pytest_plugins = ["test_github_releases"]


def test_manual_channel_disables_remote_discovery(host_paths, github):
    from robopark_host.github_releases import check_latest_release

    env = host_paths.etc / "updater.env"
    env.write_text(env.read_text().replace("GITHUB_CHANNEL=stable", "ROBOPARK_UPDATE_CHANNEL=manual"))
    assert config(host_paths).channel == "manual"
    assert check_latest_release(config(host_paths), github) is None
    assert github.calls == []


def test_legacy_prerelease_channel_is_normalized_to_rc(host_paths, github):
    env = host_paths.etc / "updater.env"
    env.write_text(env.read_text().replace("GITHUB_CHANNEL=stable", "GITHUB_CHANNEL=prerelease"))
    assert config(host_paths).channel == "rc"


def test_promotion_preserves_digest_and_requires_eligible_channel():
    from robopark_host.github_releases import promote_release
    from robopark_host.release import ReleaseError

    candidate = {
        "app_version": "1.0.0",
        "sha256": "a" * 64,
        "eligible_channels": ["rc", "stable"],
    }
    promoted = promote_release(candidate, "stable", "a" * 64)
    assert promoted == {**candidate, "channel": "stable"}

    import pytest

    with pytest.raises(ReleaseError, match="promotion_digest_mismatch"):
        promote_release(candidate, "stable", "b" * 64)
    with pytest.raises(ReleaseError, match="promotion_channel_ineligible"):
        promote_release({**candidate, "eligible_channels": ["rc"]}, "stable", "a" * 64)
