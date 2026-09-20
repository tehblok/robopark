from robopark_api.services.offline_sync import _ordered
from robopark_api.sync_schemas import SyncActionIn


def test_two_hundred_clients_keep_batches_bounded_and_dependency_order_stable():
    for client in range(200):
        prefix = f"client-{client}"
        actions = [
            SyncActionIn(
                client_action_id=f"{prefix}-comment",
                resource_type="tracker_issue",
                resource_id="ROBOPARK-1",
                action="comment",
                idempotency_key=f"{prefix}-comment-key",
                payload={"text": "ok"},
            ),
            SyncActionIn(
                client_action_id=f"{prefix}-review",
                resource_type="tracker_issue",
                resource_id="ROBOPARK-1",
                action="submit_review",
                idempotency_key=f"{prefix}-review-key",
                dependencies=[f"{prefix}-comment"],
            ),
        ]
        ordered = _ordered(list(reversed(actions)))
        assert [item.action for item in ordered] == ["comment", "submit_review"]
