import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import get_current_user, require_roles
from app.services.demand_group_merge_service import resolve_group_id, resolve_group_ids
from app.core.supabase import get_supabase_client
from app.services.demand_group_submission_service import try_group_new_demand

logger = logging.getLogger("markettwin.api.grouping")
router = APIRouter()


@router.post("/admin/demand-groups/generate")
async def generate_demand_groups(current_user=Depends(require_roles("admin"))):
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Legacy demand-group generation is disabled; use the reviewed submission workflow.",
    )


@router.post("/admin/demands/{demand_id}/grouping/retry")
async def retry_reviewed_demand_grouping(
    demand_id: str,
    current_user=Depends(require_roles("admin")),
):
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured.")

    demand_response = (
        client.table("demands")
        .select("*")
        .eq("id", demand_id)
        .limit(1)
        .execute()
    )
    demand_rows = getattr(demand_response, "data", None)
    if not isinstance(demand_rows, list) or not demand_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demand not found.")
    demand = demand_rows[0]
    if (
        demand.get("group_id") is not None
        or demand.get("grouping_status") != "needs_review"
        or demand.get("status") not in {"Submitted", "Under Review"}
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an ungrouped, active demand in needs_review can be retried.",
        )

    normalized_response = (
        client.table("demand_normalized_requirements")
        .select("demand_id")
        .eq("demand_id", demand_id)
        .eq("extraction_version", "v1")
        .limit(1)
        .execute()
    )
    normalized_rows = getattr(normalized_response, "data", None)
    if not isinstance(normalized_rows, list) or not normalized_rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The existing normalized requirements are unavailable; retry was not started.",
        )

    for table in (
        "demand_group_members",
        "merchant_offers",
        "customer_offer_selections",
        "customer_offer_eligibility",
        "offer_comparison_history",
        "orders",
    ):
        related_response = (
            client.table(table)
            .select("id")
            .eq("demand_id", demand_id)
            .limit(1)
            .execute()
        )
        related_rows = getattr(related_response, "data", None)
        if not isinstance(related_rows, list):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Demand grouping eligibility could not be verified.",
            )
        if related_rows:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Downstream records exist for this demand; retry was not started.",
            )

    try:
        result = await try_group_new_demand(demand, client)
        diagnostics_response = (
            client.table("demand_grouping_candidate_diagnostics")
            .select("candidate_group_id, candidate_demand_ids, cosine_distance, assessment_status, reason_codes, evaluated_at")
            .eq("demand_id", demand_id)
            .execute()
        )
        diagnostics = getattr(diagnostics_response, "data", None)
        if not isinstance(diagnostics, list):
            raise RuntimeError("candidate_diagnostics_read_invalid")
        return {"demand": result, "candidate_diagnostics": diagnostics}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Reviewed demand grouping retry failed (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demand grouping retry could not be completed.",
        ) from exc


@router.get("/admin/demand-groups")
async def list_demand_groups(
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(require_roles("admin")),
):
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured.")
    query = client.table("demand_groups").select("*", count="exact")
    if status:
        query = query.eq("group_status", status)
    query = query.order("created_at", desc=True)
    offset = (page - 1) * page_size
    result = query.range(offset, offset + page_size - 1).execute()
    return {"items": result.data or [], "total": result.count or 0, "page": page, "page_size": page_size}


@router.get("/admin/demand-groups/{group_id}")
async def get_demand_group(group_id: str, current_user=Depends(require_roles("admin"))):
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured.")
    group_id = resolve_group_id(group_id, client=client)
    group = client.table("demand_groups").select("*").eq("id", group_id).single().execute()
    if not group.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    members = client.table("demand_group_members").select("*").eq("group_id", group_id).execute()
    return {"group": group.data, "members": members.data or []}


@router.post("/admin/demand-groups/{group_id}/review")
async def review_demand_group(group_id: str, payload: Dict[str, Any], current_user=Depends(require_roles("admin"))):
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured.")
    group_id = resolve_group_id(group_id, client=client)
    action = payload.get("action")
    notes = payload.get("notes")
    group = client.table("demand_groups").select("*").eq("id", group_id).single().execute()
    if not group.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    prev_status = group.data.get("group_status")
    new_status = prev_status
    if action == "approve":
        new_status = "Approved"
    elif action == "reject":
        new_status = "Rejected"
    client.table("demand_groups").update({"group_status": new_status, "updated_at": "now()"}).eq("id", group_id).execute()
    client.table("demand_group_reviews").insert({
        "group_id": group_id,
        "reviewed_by": current_user["id"],
        "action": action or "review",
        "review_notes": notes,
        "previous_status": prev_status,
        "new_status": new_status,
    }).execute()
    return {"id": group_id, "status": new_status}


@router.get("/customer/groups")
async def customer_groups(current_user=Depends(require_roles("customer"))):
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured.")
    # Find demand_ids for this customer
    demands = client.table("demands").select("id").eq("customer_id", current_user["id"]).execute()
    demand_ids = [d["id"] for d in (demands.data or [])]
    if not demand_ids:
        return {"items": []}
    members = (
        client.table("demand_group_members")
        .select("group_id")
        .eq("membership_status", "active")
        .in_("demand_id", demand_ids)
        .execute()
    )
    group_ids = sorted({m["group_id"] for m in (members.data or [])})
    if not group_ids:
        return {"items": []}
    canonical_ids = sorted(set(resolve_group_ids(group_ids, client=client).values()))
    groups = client.table("demand_groups").select("*").in_("id", canonical_ids).execute()
    return {"items": groups.data or []}


@router.get("/customer/groups/{group_id}")
async def customer_group_detail(group_id: str, current_user=Depends(require_roles("customer"))):
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured.")
    group_id = resolve_group_id(group_id, client=client)
    group = client.table("demand_groups").select("*").eq("id", group_id).single().execute()
    if not group.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    # verify membership
    demands = client.table("demands").select("id").eq("customer_id", current_user["id"]).execute()
    demand_ids = [d["id"] for d in (demands.data or [])]
    members = (
        client.table("demand_group_members")
        .select("*")
        .eq("group_id", group_id)
        .eq("membership_status", "active")
        .in_("demand_id", demand_ids)
        .execute()
    )
    if not members.data:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return {"group": group.data, "membership": members.data}