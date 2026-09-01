"""Verify that report attachment metadata matches persistent storage."""

from robopark_api.db import SessionLocal
from robopark_api.services.report_attachments import (
    AttachmentStorageError,
    validate_attachment_storage,
)


def main() -> None:
    db = SessionLocal()
    try:
        validate_attachment_storage(db)
    except AttachmentStorageError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        db.close()
    print("report attachment storage verified")


if __name__ == "__main__":
    main()
