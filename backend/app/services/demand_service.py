import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase import get_supabase_client, get_supabase_service_client
from app.services.demand_mapping import (
    database_row_to_normalized_demand,
    demand_request_to_database_payload,
)
from app.services.demand_group_submission_service import try_group_new_demand

EDITABLE_STATUSES = {"Submitted", "Under Review"}
CANCELLABLE_STATUSES = {"Submitted", "Under Review"}
logger = logging.getLogger("markettwin.demands")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|api[_-]?key|service[_-]?role[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*[^,\s;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(?:bearer|basic)\s+[^\s,;]+")
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)


class DemandPersistenceError(RuntimeError):
    def __init__(self, public_message: str, diagnostics: Dict[str, Optional[str]]):
        super().__init__(public_message)
        self.public_message = public_message
        self.diagnostics = diagnostics


def _safe_error_field(value: Any) -> Optional[str]:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    text = re.sub(r"\{[^{}]*\}", "[redacted object]", text)
    text = _SECRET_PATTERN.sub(r"\1=[redacted]", text)
    text = _BEARER_PATTERN.sub("[redacted authorization]", text)
    text = _JWT_PATTERN.sub("[redacted token]", text)
    text = _EMAIL_PATTERN.sub("[redacted email]", text)
    text = _UUID_PATTERN.sub("[redacted id]", text)
    return text[:500] or None


def _postgrest_diagnostics(error: Exception) -> Dict[str, Optional[str]]:
    diagnostics = {
        name: _safe_error_field(getattr(error, name, None))
        for name in ("code", "message", "details", "hint")
    }
    raw_status = getattr(error, "status_code", getattr(error, "status", None))
    try:
        status = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status = None
    diagnostics["http_status"] = str(status) if status is not None and 100 <= status <= 599 else None
    return diagnostics


def _rpc_failure_message(diagnostics: Dict[str, Optional[str]]) -> str:
    code = diagnostics.get("code")
    http_status = diagnostics.get("http_status")
    if code in {"PGRST202", "PGRST106"}:
        return (
            "Demand submission is unavailable because its database RPC is not exposed to PostgREST. "
            "An administrator should verify migrations 020-021, the public RPC signature and grant, "
            "and reload the PostgREST schema cache if the migrations are applied."
        )
    if http_status == "404":
        return (
            "Demand submission received an unexpected database 404. "
            "An administrator should verify the RPC signature, API schema exposure, and schema cache."
        )
    if code == "42501" or http_status in {"401", "403"}:
        return (
            "Demand submission is blocked by database permissions. "
            "An administrator should verify the backend service-role configuration and RPC grant."
        )
    return "Demand could not be saved. Your reviewed form has not been cleared; please retry."


def _requirement_map(value: Any) -> Dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return {
            str(item["name"]): str(item["value"])
            for item in value
            if isinstance(item, dict) and item.get("name") and item.get("value") is not None
        }
    return {}


def _client():
    return get_supabase_client()


async def create_demand(
    customer_id: str,
    payload: Dict[str, Any],
    submission_id: str,
) -> Dict[str, Any]:
    client = get_supabase_service_client()
    if client is None:
        raise DemandPersistenceError(
            "Demand submission is unavailable because the backend service-role client is not configured. Contact an administrator.",
            {
                "code": "SERVICE_ROLE_CLIENT_UNAVAILABLE",
                "message": None,
                "details": None,
                "hint": None,
                "http_status": None,
            },
        )
    data = demand_request_to_database_payload(customer_id, payload, submission_id)
    try:
        result = client.rpc(
            "create_customer_demand_v2",
            {"p_submission_id": submission_id, "p_demand": data},
        ).execute()
    except Exception as exc:
        diagnostics = _postgrest_diagnostics(exc)
        raise DemandPersistenceError(_rpc_failure_message(diagnostics), diagnostics) from exc
    demand = result.data
    if isinstance(demand, list):
        demand = demand[0] if demand else None
    if not isinstance(demand, dict) or not demand.get("id") or not demand.get("demand_code"):
        raise RuntimeError("Demand creation failed.")
    try:
        demand = await try_group_new_demand(demand, client)
    except Exception:
        logger.warning("Demand was saved, but semantic grouping requires review.")
        demand = {
            **demand,
            "grouping_status": "needs_review",
            "grouping_review_reason": "compatibility_assessment_failed",
        }
    try:
        client.table("notifications").insert({
            "user_id": customer_id,
            "title": "Demand submitted successfully",
            "description": f"Your demand {demand['demand_code']} has been submitted.",
            "type": "demand",
            "unread": True,
        }).execute()
    except Exception:
        logger.warning("Demand was saved, but its notification could not be created.")
    return demand


def list_demands(customer_id: str, page: int = 1, page_size: int = 20, status: Optional[str] = None, search: Optional[str] = None) -> tuple[List[Dict[str, Any]], int]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")
    query = (
        client.table("demands")
        .select(
            "*,demand_groups!demands_group_id_fkey(group_reference,participating_demand_count,participants_count)",
            count="exact",
        )
        .eq("customer_id", customer_id)
    )
    if status:
        query = query.eq("status", status)
    if search:
        query = query.ilike("title", f"%{search}%")
    query = query.order("created_at", desc=True)
    offset = (page - 1) * page_size
    result = query.range(offset, offset + page_size - 1).execute()
    return result.data or [], result.count or 0


def get_demand(customer_id: str, demand_id: str) -> Optional[Dict[str, Any]]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")
    result = (
        client.table("demands")
        .select("*")
        .eq("id", demand_id)
        .eq("customer_id", customer_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_analysis_dataset(
    customer_id: str,
    demand_id: str,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")

    current_result = (
        client.table("demands")
        .select("*")
        .eq("id", demand_id)
        .eq("customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if not current_result.data:
        raise PermissionError("Demand not found or access denied.")
    current = current_result.data[0]

    fields = (
        "id,customer_id,demand_code,title,category,brand,model_number,"
        "technical_specifications,dimensions,target_quantity,unit,"
        "min_target_unit_price,max_target_unit_price,currency,expected_lead_time,status,created_at,"
        "demand_normalized_requirements(product_type,normalized_product_name,"
        "normalized_category,normalized_material,normalized_specifications,"
        "normalized_dimensions,mandatory_requirements,optional_preferences,"
        "commercial_constraints,delivery_constraints,status)"
    )
    statuses = [
        "Pending", "Submitted", "Under Review", "Grouped", "Offers Received",
        "Approved", "Completed", "Bidding Open", "Offers Available", "Accepted",
    ]
    records: List[Dict[str, Any]] = []
    offset = 0
    page_size = 500
    expected_count: Optional[int] = None
    while True:
        result = (
            client.table("demands")
            .select(fields, count="exact")
            .in_("status", statuses)
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = result.data or []
        if result.count is not None:
            expected_count = result.count
        for row in page:
            normalized = row.pop("demand_normalized_requirements", None) or []
            if isinstance(normalized, list):
                normalized = normalized[0] if normalized else {}
            records.append(database_row_to_normalized_demand(row, normalized))
        if expected_count is not None and offset + len(page) >= expected_count:
            break
        if len(page) < page_size:
            raise RuntimeError("Demand analysis pagination returned an incomplete dataset.")
        offset += len(page)

    normalized_result = (
        client.table("demand_normalized_requirements")
        .select(
            "demand_id,product_type,normalized_product_name,normalized_category,"
            "normalized_material,normalized_specifications,normalized_dimensions,"
            "mandatory_requirements,optional_preferences,commercial_constraints,"
            "delivery_constraints,status"
        )
        .eq("demand_id", demand_id)
        .limit(1)
        .execute()
    )
    current_normalized = normalized_result.data[0] if normalized_result.data else {}
    current = database_row_to_normalized_demand(current, current_normalized)
    return current, records


def get_attachments(demand_id: str) -> List[Dict[str, Any]]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")
    result = client.table("demand_attachments").select("*").eq("demand_id", demand_id).execute()
    return result.data or []


def update_demand(customer_id: str, demand_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")
    existing = get_demand(customer_id, demand_id)
    if not existing:
        raise PermissionError("Demand not found or access denied.")
    if existing.get("status") not in EDITABLE_STATUSES:
        raise ValueError("Demand is not eligible for modification.")
    if "mandatory_requirements" in payload and "optional_preferences" in payload:
        normalized_result = (
            client.table("demand_normalized_requirements")
            .select("*")
            .eq("demand_id", demand_id)
            .limit(1)
            .execute()
        )
        normalized = normalized_result.data[0] if normalized_result.data else {}
        delivery = normalized.get("delivery_constraints")
        delivery = delivery if isinstance(delivery, dict) else {}
        merged_payload = {
            "product_name": existing.get("title"),
            "category": existing.get("category"),
            "description": existing.get("description"),
            "brand": existing.get("brand"),
            "model_number": existing.get("model_number"),
            "technical_specifications": existing.get("technical_specifications") or [],
            "dimensions": existing.get("dimensions") or {},
            "material": normalized.get("normalized_material"),
            "certifications": existing.get("certifications") or [],
            "quantity": existing.get("target_quantity"),
            "unit": existing.get("unit"),
            "minimum_target_unit_price": existing.get("min_target_unit_price"),
            "maximum_target_unit_price": existing.get("max_target_unit_price"),
            "currency": existing.get("currency"),
            "required_delivery_date": delivery.get("required_delivery_date"),
            "expected_lead_time": existing.get("expected_lead_time"),
            "delivery_address": existing.get("delivery_address") or {},
            "additional_requirements": existing.get("additional_requirements"),
            "mandatory_requirements": _requirement_map(
                normalized.get("mandatory_requirements")
            ),
            "optional_preferences": _requirement_map(
                normalized.get("optional_preferences")
            ),
        }
        merged_payload.update(payload)
        material = next(
            (
                value
                for requirement_map in (
                    merged_payload.get("mandatory_requirements", {}),
                    merged_payload.get("optional_preferences", {}),
                )
                for name, value in requirement_map.items()
                if name.casefold() == "material"
            ),
            None,
        )
        if material is not None:
            merged_payload["material"] = material
        update_data = demand_request_to_database_payload(
            customer_id,
            merged_payload,
        )
        result = client.rpc(
            "update_customer_demand_v1",
            {
                "p_demand_id": demand_id,
                "p_customer_id": customer_id,
                "p_demand": update_data,
            },
        ).execute()
        demand = result.data
        if isinstance(demand, list):
            demand = demand[0] if demand else None
        if not isinstance(demand, dict) or demand.get("id") != demand_id:
            raise RuntimeError("Demand update failed.")
        return demand
    field_mapping = {
        "product_name": "title",
        "category": "category",
        "description": "description",
        "brand": "brand",
        "model_number": "model_number",
        "technical_specifications": "technical_specifications",
        "dimensions": "dimensions",
        "certifications": "certifications",
        "quantity": "target_quantity",
        "unit": "unit",
        "minimum_target_unit_price": "min_target_unit_price",
        "maximum_target_unit_price": "max_target_unit_price",
        "currency": "currency",
        "expected_lead_time": "expected_lead_time",
        "delivery_address": "delivery_address",
        "additional_requirements": "additional_requirements",
    }
    update_data = {}
    for api_field, db_field in field_mapping.items():
        if api_field in payload and payload[api_field] is not None:
            update_data[db_field] = payload[api_field]
    minimum = update_data.get("min_target_unit_price", existing.get("min_target_unit_price"))
    maximum = update_data.get("max_target_unit_price", existing.get("max_target_unit_price"))
    if minimum is not None and maximum is not None and maximum < minimum:
        raise ValueError("Maximum target unit price must be greater than or equal to the minimum.")
    if ("min_target_unit_price" in update_data) != ("max_target_unit_price" in update_data):
        if minimum is None or maximum is None:
            raise ValueError("Both target unit prices are required.")
    if not update_data:
        return existing
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = client.table("demands").update(update_data).eq("id", demand_id).eq("customer_id", customer_id).execute()
    if not result.data:
        raise RuntimeError("Demand update failed.")
    return result.data[0]


def cancel_demand(customer_id: str, demand_id: str) -> Dict[str, Any]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")
    existing = get_demand(customer_id, demand_id)
    if not existing:
        raise PermissionError("Demand not found or access denied.")
    if existing.get("status") not in CANCELLABLE_STATUSES:
        raise ValueError("Demand cannot be cancelled in its current state.")
    result = client.table("demands").update({
        "status": "Cancelled",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", demand_id).eq("customer_id", customer_id).execute()
    if not result.data:
        raise RuntimeError("Demand cancellation failed.")
    return result.data[0]


def add_attachment(demand_id: str, customer_id: str, file_name: str, file_path: str, file_type: Optional[str], file_size: Optional[int]) -> Dict[str, Any]:
    client = _client()
    if client is None:
        raise RuntimeError("Supabase is not configured.")
    existing = get_demand(customer_id, demand_id)
    if not existing:
        raise PermissionError("Demand not found or access denied.")
    result = client.table("demand_attachments").insert({
        "demand_id": demand_id,
        "file_name": file_name,
        "file_path": file_path,
        "file_type": file_type,
        "file_size": file_size,
    }).execute()
    if not result.data:
        raise RuntimeError("Attachment creation failed.")
    return result.data[0]