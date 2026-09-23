from collections.abc import Callable
from typing import Literal, TypeVar
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.inventory_schemas import (
    InventoryCatalogComponentCreateIn,
    InventoryCatalogComponentListOut,
    InventoryCatalogComponentOut,
    InventoryCatalogComponentUpdateIn,
    InventoryCatalogDeleteSummaryOut,
    InventoryCatalogPartCreateIn,
    InventoryCatalogPartMergeIn,
    InventoryCatalogPartOut,
    InventoryCatalogPartUpdateIn,
    InventoryCatalogSearchItem,
    InventoryCatalogSearchOut,
    InventoryComponentOut,
    InventoryCountCreateIn,
    InventoryCountListOut,
    InventoryCountOut,
    InventoryCountUpdateIn,
    InventoryMovementIn,
    InventoryMovementOut,
    InventoryOverviewOut,
    InventoryPartOut,
    InventoryPartUpdateIn,
    InventoryReceiptCreateIn,
    InventoryReceiptListOut,
    InventoryReceiptOut,
    InventoryReceiptReversalIn,
    InventoryReceiptRevisionIn,
    InventoryReceiptUpdateIn,
    InventoryStockOut,
    InventoryStockUpdateIn,
    InventoryTaskWriteoffIn,
)
from robopark_api.models import INVENTORY_INT64_MAX, User
from robopark_api.services import inventory as service
from robopark_api.services import (
    inventory_access,
    inventory_catalog,
    inventory_counts,
    inventory_deletion,
    inventory_exports,
    inventory_receipts,
    inventory_stock,
)
from robopark_api.services.tracker_client import MAX_ATTACHMENT_BYTES

router = APIRouter(prefix="/inventory", tags=["inventory"])
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except inventory_stock.InventoryValidation as exc:
        raise HTTPException(422, str(exc)) from exc
    except inventory_stock.InventoryConflict as exc:
        raise HTTPException(409, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc) or "forbidden") from exc
    except RuntimeError as exc:
        raise HTTPException(
            503 if str(exc) == "tracker_token_not_configured" else 502, str(exc)
        ) from exc


async def _photo(upload: UploadFile | None):
    if upload is None:
        return None
    return upload.filename, await upload.read(MAX_ATTACHMENT_BYTES + 1), upload.content_type


@router.get("/export")
def export_inventory(
    park_id: int | None = None,
    scope: Literal["all"] | None = None,
    format: Literal["csv", "xlsx"] = "xlsx",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        media_type, filename, body = _run(
            lambda: inventory_exports.build_inventory_export(
                db,
                user,
                park_id=park_id,
                all_parks=scope == "all",
                format=format,
            )
        )
    except OverflowError as exc:
        raise HTTPException(413, str(exc)) from exc
    safe_filename = f"inventory-{'all' if scope == 'all' else f'park-{park_id}'}.{format}"
    disposition = f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        body, media_type=media_type, headers={"Content-Disposition": disposition}
    )


@router.get("/parks/{park_id}/counts", response_model=InventoryCountListOut)
def list_inventory_counts(
    park_id: int,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "inventory_pagination_invalid")
    rows, total = _run(
        lambda: inventory_counts.list_counts(
            db, user, park_id=park_id, query=q, limit=limit, offset=offset
        )
    )
    return {
        "items": inventory_counts.counts_out(db, rows),
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@router.post(
    "/parks/{park_id}/counts",
    response_model=InventoryCountOut,
    status_code=status.HTTP_201_CREATED,
)
def create_inventory_count(
    park_id: int,
    payload: InventoryCountCreateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(lambda: inventory_counts.create_count(db, user, park_id=park_id, payload=payload))
    return inventory_counts.count_out(db, row)


@router.patch("/parks/{park_id}/counts/{count_id}", response_model=InventoryCountOut)
def update_inventory_count(
    park_id: int,
    count_id: int,
    payload: InventoryCountUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_counts.update_count_lines(
            db,
            user,
            park_id=park_id,
            count_id=count_id,
            lines=payload.lines,
        )
    )
    return inventory_counts.count_out(db, row)


@router.post("/parks/{park_id}/counts/{count_id}/post", response_model=InventoryCountOut)
def post_inventory_count(
    park_id: int,
    count_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(lambda: inventory_counts.post_count(db, user, park_id=park_id, count_id=count_id))
    return inventory_counts.count_out(db, row)


@router.post("/parks/{park_id}/counts/{count_id}/refresh", response_model=InventoryCountOut)
def refresh_inventory_count(
    park_id: int,
    count_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_counts.refresh_count_snapshot(
            db, user, park_id=park_id, count_id=count_id
        )
    )
    return inventory_counts.count_out(db, row)


@router.post("/parks/{park_id}/counts/{count_id}/cancel", response_model=InventoryCountOut)
def cancel_inventory_count(
    park_id: int,
    count_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(lambda: inventory_counts.cancel_count(db, user, park_id=park_id, count_id=count_id))
    return inventory_counts.count_out(db, row)


@router.delete("/counts/{count_id}", status_code=status.HTTP_204_NO_CONTENT)
def permanently_delete_inventory_count(
    count_id: int,
    permanent: bool = False,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not permanent:
        raise HTTPException(400, "inventory_permanent_delete_required")
    _run(lambda: inventory_counts.permanently_delete_count(db, user, count_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/parks/{park_id}/receipts", response_model=InventoryReceiptListOut)
def list_inventory_receipts(
    park_id: int,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "inventory_pagination_invalid")
    rows, total = _run(
        lambda: inventory_receipts.list_receipts(
            db, user, park_id=park_id, query=q, limit=limit, offset=offset
        )
    )
    return {
        "items": inventory_receipts.receipts_out(db, rows),
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@router.post(
    "/parks/{park_id}/receipts",
    response_model=InventoryReceiptOut,
    status_code=status.HTTP_201_CREATED,
)
def create_inventory_receipt(
    park_id: int,
    payload: InventoryReceiptCreateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_receipts.create_receipt(db, user, park_id=park_id, payload=payload)
    )
    return inventory_receipts.receipt_out(db, row)


@router.patch("/parks/{park_id}/receipts/{receipt_id}", response_model=InventoryReceiptOut)
def update_inventory_receipt(
    park_id: int,
    receipt_id: int,
    payload: InventoryReceiptUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_receipts.update_receipt(
            db,
            user,
            park_id=park_id,
            receipt_id=receipt_id,
            revision=payload.revision,
            changes=payload.model_dump(exclude_unset=True, exclude={"revision"}),
        )
    )
    return inventory_receipts.receipt_out(db, row)


@router.post("/parks/{park_id}/receipts/{receipt_id}/post", response_model=InventoryReceiptOut)
def post_inventory_receipt(
    park_id: int,
    receipt_id: int,
    payload: InventoryReceiptRevisionIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_receipts.post_receipt(
            db, user, park_id=park_id, receipt_id=receipt_id, revision=payload.revision
        )
    )
    return inventory_receipts.receipt_out(db, row)


@router.post("/parks/{park_id}/receipts/{receipt_id}/cancel", response_model=InventoryReceiptOut)
def cancel_inventory_receipt(
    park_id: int,
    receipt_id: int,
    payload: InventoryReceiptRevisionIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_receipts.cancel_receipt(
            db, user, park_id=park_id, receipt_id=receipt_id, revision=payload.revision
        )
    )
    return inventory_receipts.receipt_out(db, row)


@router.post("/parks/{park_id}/receipts/{receipt_id}/reverse", response_model=InventoryReceiptOut)
def reverse_inventory_receipt(
    park_id: int,
    receipt_id: int,
    payload: InventoryReceiptReversalIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_receipts.reverse_receipt(
            db,
            user,
            park_id=park_id,
            receipt_id=receipt_id,
            reason=payload.reason,
        )
    )
    return inventory_receipts.receipt_out(db, row)


@router.get("/catalog/search", response_model=InventoryCatalogSearchOut)
def search_catalog(
    park_id: int,
    q: str | None = None,
    component_id: int | None = None,
    stock_filter: str | None = None,
    mode: Literal["active", "archived", "all"] = "active",
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "inventory_pagination_invalid")
    return _run(
        lambda: inventory_catalog.search_catalog(
            db,
            user,
            park_id=park_id,
            query=q,
            component_id=component_id,
            stock_filter=stock_filter,
            mode=mode,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "/catalog/components",
    response_model=InventoryCatalogComponentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_catalog_component(
    payload: InventoryCatalogComponentCreateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_catalog.create_component(
            db, user, park_id=payload.park_id, name=payload.name
        )
    )
    return inventory_catalog.component_out(row)


@router.get("/catalog/components", response_model=InventoryCatalogComponentListOut)
def list_catalog_components(
    park_id: int,
    limit: int = 200,
    offset: int = 0,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "inventory_pagination_invalid")
    return _run(
        lambda: inventory_catalog.list_components(
            db, user, park_id=park_id, limit=limit, offset=offset
        )
    )


@router.patch("/catalog/components/{component_id}", response_model=InventoryCatalogComponentOut)
def update_catalog_component(
    component_id: int,
    payload: InventoryCatalogComponentUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_catalog.update_component(
            db, user, component_id, payload.model_dump(exclude_unset=True)
        )
    )
    return inventory_catalog.component_out(row)


@router.put(
    "/catalog/components/{component_id}/photo",
    response_model=InventoryCatalogComponentOut,
)
async def replace_catalog_component_photo(
    component_id: int,
    response: Response,
    photo: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    photo_data = await _photo(photo)
    row, cleanup_pending = _run(
        lambda: inventory_catalog.set_catalog_photo(
            db, user, kind="component", row_id=component_id, photo=photo_data
        )
    )
    if cleanup_pending:
        response.status_code = status.HTTP_202_ACCEPTED
    return inventory_catalog.component_out(row)


@router.delete(
    "/catalog/components/{component_id}/photo",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_catalog_component_photo(
    component_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    cleanup_pending = _run(
        lambda: inventory_catalog.remove_catalog_photo(
            db, user, kind="component", row_id=component_id
        )
    )
    return Response(
        status_code=(status.HTTP_202_ACCEPTED if cleanup_pending else status.HTTP_204_NO_CONTENT)
    )


@router.delete(
    "/catalog/components/{component_id}",
    response_model=InventoryCatalogDeleteSummaryOut,
)
def permanently_delete_catalog_component(
    component_id: int,
    response: Response,
    permanent: bool = False,
    q: str | None = None,
    mode: str = "active",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not permanent:
        raise HTTPException(400, "inventory_permanent_delete_required")
    result = _run(
        lambda: inventory_deletion.delete_component(
            db, user, component_id, query=q, mode=mode
        )
    )
    if result.pop("_cleanup_pending", False):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.post(
    "/catalog/parts",
    response_model=InventoryCatalogPartOut,
    status_code=status.HTTP_201_CREATED,
)
def create_catalog_part(
    payload: InventoryCatalogPartCreateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_catalog.create_part(
            db,
            user,
            park_id=payload.park_id,
            component_id=payload.component_id,
            name=payload.name,
            article=payload.article,
        )
    )
    return inventory_catalog.part_out(row)


@router.patch("/catalog/parts/{part_id}", response_model=InventoryCatalogPartOut)
def update_catalog_part(
    part_id: int,
    payload: InventoryCatalogPartUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: inventory_catalog.update_part(
            db, user, part_id, payload.model_dump(exclude_unset=True)
        )
    )
    return inventory_catalog.part_out(row)


@router.put(
    "/catalog/parts/{part_id}/photo",
    response_model=InventoryCatalogPartOut,
)
async def replace_catalog_part_photo(
    part_id: int,
    response: Response,
    photo: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    photo_data = await _photo(photo)
    row, cleanup_pending = _run(
        lambda: inventory_catalog.set_catalog_photo(
            db, user, kind="part", row_id=part_id, photo=photo_data
        )
    )
    if cleanup_pending:
        response.status_code = status.HTTP_202_ACCEPTED
    return inventory_catalog.part_out(row)


@router.delete(
    "/catalog/parts/{part_id}/photo",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_catalog_part_photo(
    part_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    cleanup_pending = _run(
        lambda: inventory_catalog.remove_catalog_photo(db, user, kind="part", row_id=part_id)
    )
    return Response(
        status_code=(status.HTTP_202_ACCEPTED if cleanup_pending else status.HTTP_204_NO_CONTENT)
    )


@router.delete(
    "/catalog/parts/{part_id}",
    response_model=InventoryCatalogDeleteSummaryOut,
)
def permanently_delete_catalog_part(
    part_id: int,
    response: Response,
    permanent: bool = False,
    q: str | None = None,
    mode: str = "active",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not permanent:
        raise HTTPException(400, "inventory_permanent_delete_required")
    result = _run(
        lambda: inventory_deletion.delete_part(db, user, part_id, query=q, mode=mode)
    )
    if result.pop("_cleanup_pending", False):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.get("/catalog/parts/{part_id}", response_model=InventoryCatalogSearchItem)
def get_catalog_part(
    part_id: int,
    park_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(lambda: inventory_catalog.catalog_item(db, user, park_id=park_id, part_id=part_id))


@router.post("/catalog/parts/{part_id}/merge", response_model=InventoryCatalogPartOut)
def merge_catalog_part(
    part_id: int,
    payload: InventoryCatalogPartMergeIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(lambda: inventory_catalog.merge_parts(db, user, part_id, payload.target_part_id))
    return inventory_catalog.part_out(row)


@router.put("/parks/{park_id}/stocks/{part_id}", response_model=InventoryStockOut)
def update_stock(
    park_id: int,
    part_id: int,
    payload: InventoryStockUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    def update():
        inventory_access.require_park(db, user, park_id, manage=True)
        stock = inventory_stock.ensure_stock(db, park_id=park_id, catalog_part_id=part_id)
        stock.minimum_quantity = payload.minimum_quantity
        stock.location = (payload.location or "").strip() or None
        stock.is_active = payload.is_active
        stock.updated_by = user.id
        inventory_stock.increment_stock_version(stock)
        db.commit()
        db.refresh(stock)
        service.audit_inventory_change(
            db,
            user,
            action="inventory.stock.configured",
            park_id=park_id,
            target_id=stock.catalog_part_id,
            changed_fields=payload.model_fields_set,
        )
        return stock

    return _run(update)


@router.get("", response_model=InventoryOverviewOut)
def overview(park_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _run(lambda: service.overview(db, user, park_id))


@router.post(
    "/components", response_model=InventoryComponentOut, status_code=status.HTTP_201_CREATED
)
async def create_component(
    request: Request,
    park_id: int = Form(...),
    name: str = Form(...),
    photo: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    photo_data = await _photo(photo)
    row = _run(lambda: service.create_component(db, user, park_id, name, photo_data))
    request.state.change_scopes = (f"inventory:{row.park_id}",)
    return {
        "id": row.id,
        "park_id": row.park_id,
        "name": row.name,
        "has_photo": bool(row.photo_storage_key),
        "parts": [],
    }


@router.post("/parts", response_model=InventoryPartOut, status_code=status.HTTP_201_CREATED)
async def create_part(
    park_id: int = Form(...),
    component_id: int = Form(...),
    name: str = Form(...),
    article: str = Form(...),
    quantity: int = Form(0, ge=0, le=INVENTORY_INT64_MAX),
    minimum_quantity: int = Form(0, ge=0, le=INVENTORY_INT64_MAX),
    location: str = Form(...),
    photo: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    photo_data = await _photo(photo)
    row = _run(
        lambda: service.create_part(
            db,
            user,
            park_id=park_id,
            component_id=component_id,
            name=name,
            article=article,
            quantity=quantity,
            minimum_quantity=minimum_quantity,
            location=location,
            photo=photo_data,
        )
    )
    return service.part_out(row)


@router.patch("/parts/{part_id}", response_model=InventoryPartOut)
def update_part(
    part_id: int,
    payload: InventoryPartUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return service.part_out(
        _run(lambda: service.update_part(db, user, part_id, payload.model_dump(exclude_unset=True)))
    )


@router.post(
    "/parts/{part_id}/movements",
    response_model=InventoryMovementOut,
    status_code=status.HTTP_201_CREATED,
)
def move_stock(
    part_id: int,
    payload: InventoryMovementIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: service.move_stock(
            db,
            user,
            part_id,
            park_id=payload.park_id,
            catalog_part_id=payload.catalog_part_id,
            kind=payload.kind,
            quantity=payload.quantity,
            note=payload.note,
        )
    )
    return {
        **row.__dict__,
        "part_id": service.part_adapter_id(db, row.catalog_part_id, legacy_part_id=row.part_id),
        "catalog_part_id": row.catalog_part_id,
        "actor_username": user.username,
    }


@router.post(
    "/tasks/{issue_key}/writeoff",
    response_model=InventoryMovementOut,
    status_code=status.HTTP_201_CREATED,
)
def task_writeoff(
    request: Request,
    issue_key: str,
    payload: InventoryTaskWriteoffIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: service.task_writeoff(
            db,
            user,
            issue_key,
            payload.part_id,
            payload.quantity,
            park_id=payload.park_id,
            catalog_part_id=payload.catalog_part_id,
            idempotency_key=payload.idempotency_key,
        )
    )
    request.state.change_scopes = (f"inventory:{row.park_id}", f"work:park:{row.park_id}", "work")
    return {
        **row.__dict__,
        "part_id": service.part_adapter_id(db, row.catalog_part_id, legacy_part_id=row.part_id),
        "catalog_part_id": row.catalog_part_id,
        "actor_username": user.username,
    }


@router.get("/movements", response_model=list[InventoryMovementOut])
def movements(
    park_id: int,
    limit: int = 100,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(lambda: service.movements(db, user, park_id, limit))


@router.get("/components/{component_id}/photo")
def component_photo(
    component_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    path, media_type, _filename = _run(lambda: service.component_photo(db, user, component_id))
    return FileResponse(path, media_type=media_type)


@router.get("/parts/{part_id}/photo")
def part_photo(part_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    path, media_type, _filename = _run(lambda: service.part_photo(db, user, part_id))
    return FileResponse(path, media_type=media_type)
