"""Field management API endpoints: Equipment, Materials, Permits, Punch List, Risk Register."""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.field_management import (
    Equipment,
    Material,
    Permit,
    PunchListItem,
    RiskRegisterEntry,
)
from app.models.user import User
from app.schemas.field_management import (
    EquipmentCreate,
    EquipmentListResponse,
    EquipmentResponse,
    EquipmentUpdate,
    MaterialCreate,
    MaterialListResponse,
    MaterialResponse,
    MaterialUpdate,
    PermitCreate,
    PermitListResponse,
    PermitResponse,
    PermitUpdate,
    PunchListItemCreate,
    PunchListItemListResponse,
    PunchListItemResponse,
    PunchListItemUpdate,
    RiskRegisterEntryCreate,
    RiskRegisterEntryListResponse,
    RiskRegisterEntryResponse,
    RiskRegisterEntryUpdate,
)
from app.schemas.pagination import PaginationMeta

logger = logging.getLogger(__name__)

router = APIRouter()

# Fields that must never be overwritten via generic setattr update patterns
_PROTECTED_FIELDS = frozenset({"id", "project_id", "created_at", "created_by", "org_id"})

# ---------------------------------------------------------------------------
# Equipment CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/equipment",
    response_model=EquipmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_equipment(
    request: EquipmentCreate,
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new equipment record."""
    await verify_project_access(request.project_id, current_user, db)

    equipment = Equipment(
        project_id=request.project_id,
        equipment_type=request.equipment_type,
        make=request.make,
        model=request.model,
        serial_number=request.serial_number,
        status=request.status,
        daily_rate=request.daily_rate,
        location=request.location,
        maintenance_due_date=request.maintenance_due_date,
        last_inspection_date=request.last_inspection_date,
        operator_id=request.operator_id,
        notes=request.notes,
        metadata_=request.metadata_,
    )
    db.add(equipment)
    await db.flush()
    await db.refresh(equipment)
    return equipment


@router.get(
    "/equipment",
    response_model=EquipmentListResponse,
)
async def list_equipment(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List equipment for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(Equipment)
        .where(Equipment.project_id == project_id)
        .order_by(Equipment.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(Equipment, cursor_uuid)
        if cursor_obj:
            query = query.where(Equipment.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return EquipmentListResponse(
        data=cast(list[EquipmentResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/equipment/{equipment_id}",
    response_model=EquipmentResponse,
)
async def get_equipment(
    equipment_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single equipment record by ID."""
    equipment = await db.get(Equipment, equipment_id)
    if equipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment not found")
    await verify_project_access(equipment.project_id, current_user, db)
    return equipment


@router.patch(
    "/equipment/{equipment_id}",
    response_model=EquipmentResponse,
)
async def update_equipment(
    equipment_id: uuid.UUID,
    request: EquipmentUpdate,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update an equipment record."""
    equipment = await db.get(Equipment, equipment_id)
    if equipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment not found")
    await verify_project_access(equipment.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True, by_alias=False)
    for field, value in update_data.items():
        if field not in _PROTECTED_FIELDS:
            setattr(equipment, field, value)

    await db.flush()
    await db.refresh(equipment)
    return equipment


@router.delete(
    "/equipment/{equipment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_equipment(
    equipment_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete an equipment record."""
    equipment = await db.get(Equipment, equipment_id)
    if equipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment not found")
    await verify_project_access(equipment.project_id, current_user, db)

    await db.delete(equipment)
    await db.flush()


# ---------------------------------------------------------------------------
# Material CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/materials",
    response_model=MaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_material(
    request: MaterialCreate,
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new material record."""
    await verify_project_access(request.project_id, current_user, db)

    material = Material(
        project_id=request.project_id,
        name=request.name,
        category=request.category,
        csi_code=request.csi_code,
        unit=request.unit,
        quantity_ordered=request.quantity_ordered,
        quantity_received=request.quantity_received,
        quantity_installed=request.quantity_installed,
        unit_cost=request.unit_cost,
        supplier=request.supplier,
        lead_time_days=request.lead_time_days,
        expected_delivery=request.expected_delivery,
        status=request.status,
        storage_location=request.storage_location,
        notes=request.notes,
    )
    db.add(material)
    await db.flush()
    await db.refresh(material)

    # IG-16: When a material with a CSI code is created, look up its carbon
    # factor and add the embodied carbon to the project's sustainability totals.
    # This never blocks material creation — errors are logged and swallowed.
    try:
        if material.csi_code and material.quantity_ordered:
            from decimal import Decimal as _Decimal

            from app.models.sustainability import ProjectSustainability
            from app.services.estimating.carbon_database import get_carbon_factor

            factor = get_carbon_factor(material.csi_code)
            if factor:
                quantity = float(material.quantity_ordered or 0)
                if quantity > 0:
                    item_carbon_kgco2e = factor.embodied_carbon_kgco2e * quantity

                    # Upsert ProjectSustainability
                    ps_result = await db.execute(
                        select(ProjectSustainability).where(
                            ProjectSustainability.project_id == request.project_id
                        )
                    )
                    ps = ps_result.scalars().first()
                    if ps is None:
                        ps = ProjectSustainability(
                            project_id=request.project_id,
                            total_embodied_carbon_kgco2e=_Decimal(
                                str(round(item_carbon_kgco2e, 2))
                            ),
                        )
                        db.add(ps)
                    else:
                        current = float(ps.total_embodied_carbon_kgco2e or 0)
                        ps.total_embodied_carbon_kgco2e = _Decimal(
                            str(round(current + item_carbon_kgco2e, 2))
                        )

                    await db.flush()
                    logger.info(
                        "Added %.2f kgCO2e for material '%s' (CSI %s) to project %s sustainability",
                        item_carbon_kgco2e,
                        material.name,
                        material.csi_code,
                        request.project_id,
                    )
    except Exception:
        logger.warning(
            "Failed to update sustainability carbon for material %s (CSI %s)",
            material.name,
            request.csi_code,
            exc_info=True,
        )

    return material


@router.get(
    "/materials",
    response_model=MaterialListResponse,
)
async def list_materials(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List materials for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(Material)
        .where(Material.project_id == project_id)
        .order_by(Material.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(Material, cursor_uuid)
        if cursor_obj:
            query = query.where(Material.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return MaterialListResponse(
        data=cast(list[MaterialResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/materials/{material_id}",
    response_model=MaterialResponse,
)
async def get_material(
    material_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single material record by ID."""
    material = await db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    await verify_project_access(material.project_id, current_user, db)
    return material


@router.patch(
    "/materials/{material_id}",
    response_model=MaterialResponse,
)
async def update_material(
    material_id: uuid.UUID,
    request: MaterialUpdate,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a material record."""
    material = await db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    await verify_project_access(material.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field not in _PROTECTED_FIELDS:
            setattr(material, field, value)

    await db.flush()
    await db.refresh(material)
    return material


@router.delete(
    "/materials/{material_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_material(
    material_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a material record."""
    material = await db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    await verify_project_access(material.project_id, current_user, db)

    await db.delete(material)
    await db.flush()


# ---------------------------------------------------------------------------
# Permit CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/permits",
    response_model=PermitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_permit(
    request: PermitCreate,
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new permit record."""
    await verify_project_access(request.project_id, current_user, db)

    permit = Permit(
        project_id=request.project_id,
        permit_type=request.permit_type,
        permit_number=request.permit_number,
        issuing_authority=request.issuing_authority,
        status=request.status,
        application_date=request.application_date,
        approval_date=request.approval_date,
        expiration_date=request.expiration_date,
        conditions=request.conditions,
        inspections=request.inspections,
        documents=request.documents,
        notes=request.notes,
    )
    db.add(permit)
    await db.flush()
    await db.refresh(permit)
    return permit


@router.get(
    "/permits",
    response_model=PermitListResponse,
)
async def list_permits(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List permits for a project."""
    await verify_project_access(project_id, current_user, db)

    query = select(Permit).where(Permit.project_id == project_id).order_by(Permit.created_at.desc())
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(Permit, cursor_uuid)
        if cursor_obj:
            query = query.where(Permit.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return PermitListResponse(
        data=cast(list[PermitResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/permits/{permit_id}",
    response_model=PermitResponse,
)
async def get_permit(
    permit_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single permit record by ID."""
    permit = await db.get(Permit, permit_id)
    if permit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permit not found")
    await verify_project_access(permit.project_id, current_user, db)
    return permit


@router.patch(
    "/permits/{permit_id}",
    response_model=PermitResponse,
)
async def update_permit(
    permit_id: uuid.UUID,
    request: PermitUpdate,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a permit record."""
    permit = await db.get(Permit, permit_id)
    if permit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permit not found")
    await verify_project_access(permit.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field not in _PROTECTED_FIELDS:
            setattr(permit, field, value)

    await db.flush()
    await db.refresh(permit)
    return permit


@router.delete(
    "/permits/{permit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_permit(
    permit_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a permit record."""
    permit = await db.get(Permit, permit_id)
    if permit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permit not found")
    await verify_project_access(permit.project_id, current_user, db)

    await db.delete(permit)
    await db.flush()


# ---------------------------------------------------------------------------
# Punch List Item CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/punch-list",
    response_model=PunchListItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_punch_list_item(
    request: PunchListItemCreate,
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new punch list item."""
    await verify_project_access(request.project_id, current_user, db)

    item = PunchListItem(
        project_id=request.project_id,
        item_number=request.item_number,
        description=request.description,
        location=request.location,
        category=request.category,
        priority=request.priority,
        status=request.status,
        assigned_to=request.assigned_to,
        created_by=current_user.id,
        due_date=request.due_date,
        photos=request.photos,
        notes=request.notes,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


@router.get(
    "/punch-list",
    response_model=PunchListItemListResponse,
)
async def list_punch_list_items(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List punch list items for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(PunchListItem)
        .where(PunchListItem.project_id == project_id)
        .order_by(PunchListItem.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(PunchListItem, cursor_uuid)
        if cursor_obj:
            query = query.where(PunchListItem.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return PunchListItemListResponse(
        data=cast(list[PunchListItemResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/punch-list/{item_id}",
    response_model=PunchListItemResponse,
)
async def get_punch_list_item(
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single punch list item by ID."""
    item = await db.get(PunchListItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Punch list item not found"
        )
    await verify_project_access(item.project_id, current_user, db)
    return item


@router.patch(
    "/punch-list/{item_id}",
    response_model=PunchListItemResponse,
)
async def update_punch_list_item(
    item_id: uuid.UUID,
    request: PunchListItemUpdate,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a punch list item."""
    item = await db.get(PunchListItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Punch list item not found"
        )
    await verify_project_access(item.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field not in _PROTECTED_FIELDS:
            setattr(item, field, value)

    await db.flush()
    await db.refresh(item)
    return item


@router.delete(
    "/punch-list/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_punch_list_item(
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a punch list item."""
    item = await db.get(PunchListItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Punch list item not found"
        )
    await verify_project_access(item.project_id, current_user, db)

    await db.delete(item)
    await db.flush()


# ---------------------------------------------------------------------------
# Risk Register CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/risk-register",
    response_model=RiskRegisterEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_risk_register_entry(
    request: RiskRegisterEntryCreate,
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new risk register entry."""
    await verify_project_access(request.project_id, current_user, db)

    entry = RiskRegisterEntry(
        project_id=request.project_id,
        risk_id=request.risk_id,
        description=request.description,
        category=request.category,
        probability=request.probability,
        impact=request.impact,
        risk_score=request.risk_score,
        mitigation_strategy=request.mitigation_strategy,
        contingency_plan=request.contingency_plan,
        owner_id=request.owner_id,
        status=request.status,
        trigger_conditions=request.trigger_conditions,
        response_actions=request.response_actions,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return entry


@router.get(
    "/risk-register",
    response_model=RiskRegisterEntryListResponse,
)
async def list_risk_register_entries(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List risk register entries for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(RiskRegisterEntry)
        .where(RiskRegisterEntry.project_id == project_id)
        .order_by(RiskRegisterEntry.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(RiskRegisterEntry, cursor_uuid)
        if cursor_obj:
            query = query.where(RiskRegisterEntry.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return RiskRegisterEntryListResponse(
        data=cast(list[RiskRegisterEntryResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/risk-register/{entry_id}",
    response_model=RiskRegisterEntryResponse,
)
async def get_risk_register_entry(
    entry_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single risk register entry by ID."""
    entry = await db.get(RiskRegisterEntry, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Risk register entry not found"
        )
    await verify_project_access(entry.project_id, current_user, db)
    return entry


@router.patch(
    "/risk-register/{entry_id}",
    response_model=RiskRegisterEntryResponse,
)
async def update_risk_register_entry(
    entry_id: uuid.UUID,
    request: RiskRegisterEntryUpdate,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a risk register entry."""
    entry = await db.get(RiskRegisterEntry, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Risk register entry not found"
        )
    await verify_project_access(entry.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field not in _PROTECTED_FIELDS:
            setattr(entry, field, value)

    await db.flush()
    await db.refresh(entry)
    return entry


@router.delete(
    "/risk-register/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_risk_register_entry(
    entry_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a risk register entry."""
    entry = await db.get(RiskRegisterEntry, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Risk register entry not found"
        )
    await verify_project_access(entry.project_id, current_user, db)

    await db.delete(entry)
    await db.flush()
