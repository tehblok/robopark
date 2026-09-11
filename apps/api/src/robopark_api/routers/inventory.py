from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.inventory_schemas import (
    InventoryCatalogComponentCreateIn,
    InventoryCatalogComponentOut,
    InventoryCatalogComponentUpdateIn,
    InventoryCatalogPartCreateIn,
    InventoryCatalogPartOut,
    InventoryCatalogPartUpdateIn,
    InventoryCatalogSearchOut,
    InventoryComponentOut,
    InventoryMovementIn,
    InventoryMovementOut,
    InventoryOverviewOut,
    InventoryPartOut,
    InventoryPartUpdateIn,
    InventoryStockOut,
    InventoryStockUpdateIn,
    InventoryTaskWriteoffIn,
)
from robopark_api.models import User
from robopark_api.services import inventory as service
from robopark_api.services import inventory_access, inventory_catalog, inventory_stock
from robopark_api.services.tracker_client import MAX_ATTACHMENT_BYTES

router = APIRouter(prefix="/inventory", tags=["inventory"])
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
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


@router.get("/catalog/search", response_model=InventoryCatalogSearchOut)
def search_catalog(
    park_id: int,
    q: str | None = None,
    component_id: int | None = None,
    stock_filter: str | None = None,
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
        stock.version += 1
        db.commit()
        db.refresh(stock)
        return stock

    return _run(update)


@router.get("", response_model=InventoryOverviewOut)
def overview(park_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _run(lambda: service.overview(db, user, park_id))


@router.post(
    "/components", response_model=InventoryComponentOut, status_code=status.HTTP_201_CREATED
)
async def create_component(
    park_id: int = Form(...),
    name: str = Form(...),
    photo: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    photo_data = await _photo(photo)
    row = _run(lambda: service.create_component(db, user, park_id, name, photo_data))
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
    quantity: int = Form(0, ge=0),
    minimum_quantity: int = Form(0, ge=0),
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
            db, user, part_id, kind=payload.kind, quantity=payload.quantity, note=payload.note
        )
    )
    return {
        **row.__dict__,
        "part_id": row.part_id or row.catalog_part_id,
        "actor_username": user.username,
    }


@router.post(
    "/tasks/{issue_key}/writeoff",
    response_model=InventoryMovementOut,
    status_code=status.HTTP_201_CREATED,
)
def task_writeoff(
    issue_key: str,
    payload: InventoryTaskWriteoffIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: service.task_writeoff(db, user, issue_key, payload.part_id, payload.quantity)
    )
    return {
        **row.__dict__,
        "part_id": row.part_id or row.catalog_part_id,
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
