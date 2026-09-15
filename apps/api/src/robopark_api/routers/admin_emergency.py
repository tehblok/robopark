"""Admin CRUD API for Emergency section configuration."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from robopark_api.db import get_db
from robopark_api.deps import require_builtin_admin, require_permission
from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole, User
from robopark_api.schemas import (
    EmergencyFieldAdminOut,
    EmergencyFieldCreate,
    EmergencyFieldUpdate,
    EmergencySectionAdminOut,
    EmergencySectionCreate,
    EmergencySectionsReorder,
    EmergencySectionUpdate,
)
from robopark_api.services import emergency_config, rbac

router = APIRouter(prefix="/admin/emergency", tags=["admin-emergency"])


def _section_query():
    return select(EmergencySection).options(
        selectinload(EmergencySection.fields),
        selectinload(EmergencySection.roles),
    )


def _parse_meta(meta_json: str | None) -> dict[str, Any] | None:
    if meta_json is None:
        return None
    value = json.loads(meta_json)
    return value if isinstance(value, dict) else None


def _field_out(field: EmergencyField) -> EmergencyFieldAdminOut:
    return EmergencyFieldAdminOut(
        id=field.id,
        path=field.path,
        label=field.label,
        sort_order=field.sort_order,
    )


def _section_out(section: EmergencySection) -> EmergencySectionAdminOut:
    return EmergencySectionAdminOut(
        id=section.id,
        title=section.title,
        sort_order=section.sort_order,
        is_enabled=section.is_enabled,
        formatter=section.formatter,
        meta=_parse_meta(section.meta_json),
        roles=sorted(item.role for item in section.roles),
        fields=[
            _field_out(field)
            for field in sorted(section.fields, key=lambda item: (item.sort_order, item.id))
        ],
    )


def _get_section(db: Session, section_id: str) -> EmergencySection:
    section = db.scalar(_section_query().where(EmergencySection.id == section_id))
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return section


def _get_field(db: Session, field_id: int) -> EmergencyField:
    field = db.get(EmergencyField, field_id)
    if field is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return field


def _commit_write(db: Session) -> None:
    db.commit()
    emergency_config.invalidate_config_cache()


@router.get("/sections", response_model=list[EmergencySectionAdminOut])
def list_sections(
    db: Session = Depends(get_db),
    _viewer: User = Depends(require_permission(rbac.PERMISSION_NAV_ADMIN_EMERGENCY)),
) -> list[EmergencySectionAdminOut]:
    sections = db.scalars(
        _section_query().order_by(EmergencySection.sort_order, EmergencySection.id)
    ).all()
    return [_section_out(section) for section in sections]


@router.post(
    "/sections",
    response_model=EmergencySectionAdminOut,
    status_code=status.HTTP_201_CREATED,
)
def create_section(
    payload: EmergencySectionCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> EmergencySectionAdminOut:
    if db.get(EmergencySection, payload.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    max_sort_order = db.scalar(select(func.max(EmergencySection.sort_order)))
    section = EmergencySection(
        id=payload.id,
        title=payload.title,
        sort_order=(max_sort_order + 1) if max_sort_order is not None else 0,
        is_enabled=payload.is_enabled,
        formatter=payload.formatter,
        meta_json=json.dumps(payload.meta, ensure_ascii=False) if payload.meta else None,
    )
    section.roles = [
        EmergencySectionRole(section_id=payload.id, role=role) for role in set(payload.roles)
    ]
    section.fields = [
        EmergencyField(
            section_id=payload.id,
            path=field.path,
            label=field.label,
            sort_order=sort_order,
        )
        for sort_order, field in enumerate(payload.fields)
    ]
    db.add(section)
    try:
        _commit_write(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from None
    return _section_out(_get_section(db, section.id))


@router.put("/sections/reorder", response_model=list[EmergencySectionAdminOut])
def reorder_sections(
    payload: EmergencySectionsReorder,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> list[EmergencySectionAdminOut]:
    sections = db.scalars(_section_query()).all()
    by_id = {section.id: section for section in sections}
    if len(payload.ids) != len(set(payload.ids)) or set(payload.ids) != set(by_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids_must_include_all_sections_once",
        )
    for sort_order, section_id in enumerate(payload.ids):
        by_id[section_id].sort_order = sort_order
    _commit_write(db)
    return [_section_out(by_id[section_id]) for section_id in payload.ids]


@router.patch("/sections/{section_id}", response_model=EmergencySectionAdminOut)
def update_section(
    section_id: str,
    payload: EmergencySectionUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> EmergencySectionAdminOut:
    section = _get_section(db, section_id)
    changes = payload.model_dump(exclude_unset=True)
    for key in ("title", "is_enabled", "formatter"):
        if key in changes:
            setattr(section, key, changes[key])
    if "meta" in changes:
        section.meta_json = (
            json.dumps(changes["meta"], ensure_ascii=False) if changes["meta"] else None
        )
    if "roles" in changes:
        section.roles = [
            EmergencySectionRole(section_id=section.id, role=role)
            for role in set(changes["roles"] or [])
        ]
    _commit_write(db)
    return _section_out(_get_section(db, section_id))


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_section(
    section_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> Response:
    db.delete(_get_section(db, section_id))
    _commit_write(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sections/{section_id}/fields",
    response_model=EmergencyFieldAdminOut,
    status_code=status.HTTP_201_CREATED,
)
def create_field(
    section_id: str,
    payload: EmergencyFieldCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> EmergencyFieldAdminOut:
    _get_section(db, section_id)
    sort_order = db.scalar(
        select(func.max(EmergencyField.sort_order)).where(EmergencyField.section_id == section_id)
    )
    field = EmergencyField(
        section_id=section_id,
        path=payload.path,
        label=payload.label,
        sort_order=(sort_order + 1) if sort_order is not None else 0,
    )
    db.add(field)
    _commit_write(db)
    db.refresh(field)
    return _field_out(field)


@router.patch("/fields/{field_id}", response_model=EmergencyFieldAdminOut)
def update_field(
    field_id: int,
    payload: EmergencyFieldUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> EmergencyFieldAdminOut:
    field = _get_field(db, field_id)
    changes = payload.model_dump(exclude_unset=True)
    for key in ("path", "label"):
        if key in changes:
            setattr(field, key, changes[key])
    _commit_write(db)
    db.refresh(field)
    return _field_out(field)


@router.delete("/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_field(
    field_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_builtin_admin),
) -> Response:
    db.delete(_get_field(db, field_id))
    _commit_write(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/export")
def export_config(
    db: Session = Depends(get_db),
    _viewer: User = Depends(require_permission(rbac.PERMISSION_NAV_ADMIN_EMERGENCY)),
) -> dict[str, Any]:
    sections = db.scalars(
        _section_query().order_by(EmergencySection.sort_order, EmergencySection.id)
    ).all()
    exported: dict[str, dict[str, Any]] = {}
    for section in sections:
        item: dict[str, Any] = {}
        if meta := _parse_meta(section.meta_json):
            item.update(meta)
        item["title"] = section.title
        if section.fields:
            item["fields"] = [
                {"path": field.path, "label": field.label}
                for field in sorted(section.fields, key=lambda field: (field.sort_order, field.id))
            ]
        if section.formatter:
            item["formatter"] = section.formatter
        exported[section.id] = item
    return {"sections": exported}
