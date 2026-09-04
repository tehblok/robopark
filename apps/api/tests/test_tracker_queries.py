"""Offline Tracker Query Language helpers (no network)."""

from robopark_api.services import tracker_client, tracker_metrics


def test_api_page_size_is_tracker_max():
    assert tracker_client.API_PAGE_SIZE == 50


def test_ql_token_ascii_unquoted_cyrillic_quoted():
    assert tracker_client.ql_token("Next") == "Next"
    assert tracker_client.ql_token("Сигма") == '"Сигма"'
    assert tracker_client.ql_quote('a"b') == '"a\\"b"'


def test_exclude_tag_uses_bang_not_minus():
    clause = tracker_client.exclude_tag("donor")
    assert clause == 'Tags: !"donor"'
    assert "-Tags:" not in clause


def test_open_blockers_query_for_sdcfleetops_includes_default_types():
    query = tracker_client.build_open_blockers_query("SDCFLEETOPS", "Next")
    assert "Queue: SDCFLEETOPS" in query
    assert "Priority: blocker" in query
    assert "Type: repair, service, calibration" in query
    assert "Tags: Next" in query
    assert "Resolution: empty()" in query
    assert 'Status: !"Закрыт"' in query
    assert query.count("(") == query.count(")")


def test_open_blockers_query_non_fleet_queue_skips_default_types():
    query = tracker_client.build_open_blockers_query("OTHERQ", "Next")
    assert "Type:" not in query


def test_open_blockers_explicit_type_overrides_default():
    query = tracker_client.build_open_blockers_query("SDCFLEETOPS", "Next", issue_type="incident")
    assert "Type: incident" in query
    assert "repair, service" not in query


def test_arrived_and_done_today_queries():
    arrived = tracker_metrics.build_arrived_today_query("SDCFLEETOPS", "Next")
    done = tracker_metrics.build_done_today_query("SDCFLEETOPS", "Next")
    assert "Created: today()" in arrived
    assert "Resolution:" not in arrived
    assert "Resolved: today()" in done
    assert "Updated:" not in done
    assert "Resolution: fixed" in done
    assert "Type: repair, service, calibration" in arrived


def test_waiting_parts_keeps_typo_status_key():
    query = tracker_metrics.build_status_query(
        "SDCFLEETOPS",
        "Next",
        tracker_metrics.DEFAULT_STATUS_KEYS["waiting_parts"],
    )
    assert query is not None
    assert "delieveryWaiting" in query
    assert "Ожидание поставки" in query


def test_is_issue_open_item_filters_resolved():
    assert tracker_client.is_issue_open_item({"status": "Open", "resolution": ""})
    assert not tracker_client.is_issue_open_item({"status": "Closed", "resolution": ""})
    assert not tracker_client.is_issue_open_item({"status": "Open", "resolution": "fixed"})


def test_metrics_cache_ttl_matches_bot():
    assert tracker_metrics.METRICS_CACHE_TTL_SEC == 90


def test_assignee_clause():
    assert tracker_client.assignee_clause("ivan") == "Assignee: ivan"
    assert tracker_client.assignee_clause("empty") == "Assignee: empty()"
    assert tracker_client.assignee_clause(None) is None


def test_untagged_blockers_excludes_park_tags():
    query = tracker_client.build_untagged_blockers_query(
        "SDCFLEETOPS",
        ["Next", "Сигма"],
    )
    assert "Queue: SDCFLEETOPS" in query
    assert 'Tags: !"Next"' in query
    assert 'Tags: !"Сигма"' in query
    assert "Tags: empty()" not in query
    assert "Type: repair, service, calibration" in query
    assert tracker_client.open_issues_clause() in query


def test_untagged_without_known_tags_falls_back_to_empty_tags():
    query = tracker_client.build_untagged_blockers_query("OTHERQ", [])
    assert "Tags: empty()" in query
    assert "Type:" not in query


def test_incident_blockers_query():
    query = tracker_client.build_incident_blockers_query("SDCFLEETOPS")
    assert "Type: incident" in query
    assert "Priority: blocker" in query
    assert tracker_client.open_issues_clause() in query
