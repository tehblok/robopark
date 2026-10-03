from datetime import UTC, datetime


def test_deadline_uses_the_parks_local_opening_time():
    from robopark_api.services.sla_clock import deadline

    # 05:00 UTC is 10:00 in Yekaterinburg and 08:00 in Moscow.
    assert deadline(
        datetime(2026, 9, 18, 5, tzinfo=UTC),
        timezone="Asia/Yekaterinburg",
        target_hours=5,
    ) == datetime(2026, 9, 18, 10, tzinfo=UTC)


def test_deadline_counts_weekends_across_a_daylight_saving_change():
    from robopark_api.services.sla_clock import deadline

    # Saturday 20:00 Berlin: one hour now, four after Sunday opens at 09:00 CEST.
    assert deadline(
        datetime(2026, 3, 28, 19, tzinfo=UTC),
        timezone="Europe/Berlin",
        target_hours=5,
    ) == datetime(2026, 3, 29, 11, tzinfo=UTC)


def test_elapsed_working_hours_ignores_the_closed_night():
    from robopark_api.services.sla_clock import elapsed_working_hours

    assert (
        elapsed_working_hours(
            datetime(2026, 9, 18, 17, tzinfo=UTC),
            datetime(2026, 9, 19, 10, tzinfo=UTC),
            timezone="Europe/Moscow",
        )
        == 5
    )


def test_deadline_at_closing_starts_next_day_and_never_counts_the_night():
    from robopark_api.services.sla_clock import deadline, elapsed_working_hours

    queued = datetime(2026, 9, 18, 18, tzinfo=UTC)  # 21:00 in Moscow.
    due = deadline(queued, timezone="Europe/Moscow")

    assert due == datetime(2026, 9, 19, 11, tzinfo=UTC)
    assert elapsed_working_hours(queued, due, timezone="Europe/Moscow") == 5


def test_autumn_clock_change_preserves_five_local_working_hours():
    from robopark_api.services.sla_clock import deadline, elapsed_working_hours

    queued = datetime(2026, 10, 24, 18, tzinfo=UTC)  # Saturday 20:00 in Berlin.
    due = deadline(queued, timezone="Europe/Berlin")

    assert due == datetime(2026, 10, 25, 12, tzinfo=UTC)
    assert elapsed_working_hours(queued, due, timezone="Europe/Berlin") == 5
