from collections import defaultdict
from decimal import Decimal, InvalidOperation
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from app.core.config import get_settings
from app.services.demand_compatibility import (
    assess_demand_grouping_readiness,
    assess_group_compatibility,
)
from app.services.demand_embedding_service import (
    DemandEmbeddingService,
    DemandEmbeddingResult,
    build_embedding_input,
    calculate_embedding_input_hash,
    get_demand_embedding,
    persist_demand_embedding,
    search_demand_embedding_candidates,
)
from app.services.demand_family_taxonomy import canonical_demand_family
from app.services.demand_normalization import normalize_demand_v2

logger = logging.getLogger("markettwin.demand_group_submission")


def _rows(response: Any) -> list[Mapping[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or any(not isinstance(row, Mapping) for row in data):
        raise RuntimeError("grouping_read_response_invalid")
    return data


def _rpc_data(response: Any) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, Mapping):
        raise RuntimeError("grouping_rpc_response_invalid")
    return dict(data)


def _snapshot(
    groups: Sequence[Mapping[str, Any]],
    demands: Sequence[Mapping[str, Any]],
    memberships: Sequence[Mapping[str, Any]],
    normalized: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    demands_by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for demand in demands:
        demands_by_group[str(demand.get("group_id"))].append(demand)
    result = []
    for group in sorted(groups, key=lambda row: str(row.get("id"))):
        group_id = str(group["id"])
        revisions = []
        for demand in sorted(demands_by_group.get(group_id, []), key=lambda row: str(row.get("id"))):
            normalized_row = normalized.get(str(demand["id"]), {})
            revisions.append({
                "demand_id": str(demand["id"]),
                "demand_updated_at": demand.get("updated_at"),
                "normalized_updated_at": normalized_row.get("updated_at"),
                "normalized_snapshot": {
                    key: value
                    for key, value in normalized_row.items()
                    if key not in {"created_at", "updated_at", "reviewed_at"}
                },
            })
        result.append({
            "group_id": group_id,
            "group_updated_at": group.get("updated_at"),
            "demand_ids": [item["demand_id"] for item in revisions],
            "revisions": revisions,
            "memberships": [
                {
                    key: membership.get(key)
                    for key in (
                        "customer_id", "demand_id", "quantity_committed",
                        "membership_status", "removed_at", "joined_at",
                        "original_target_price", "original_budget",
                    )
                }
                for membership in sorted(
                    (row for row in memberships if str(row.get("group_id")) == group_id),
                    key=lambda row: str(row.get("customer_id") or ""),
                )
            ],
        })
    return result


def _group_membership_is_complete(
    group_id: str,
    demands: Sequence[Mapping[str, Any]],
    memberships: Sequence[Mapping[str, Any]],
) -> bool:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for demand in demands:
        if str(demand.get("group_id")) != group_id or not demand.get("customer_id"):
            return False
        if demand.get("status") not in {"Submitted", "Under Review"}:
            return False
        grouped[str(demand["customer_id"])].append(demand)
    members_by_customer: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for membership in memberships:
        if str(membership.get("group_id")) == group_id:
            members_by_customer[str(membership.get("customer_id"))].append(membership)
    if set(grouped) != set(members_by_customer):
        return False
    for customer_id, rows in grouped.items():
        rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
        customer_memberships = members_by_customer[customer_id]
        if len(customer_memberships) != 1:
            return False
        membership = customer_memberships[0]
        if (
            membership.get("membership_status") != "active"
            or membership.get("removed_at") is not None
            or str(membership.get("demand_id") or "") != str(rows[0].get("id"))
        ):
            return False
        try:
            committed = Decimal(str(membership.get("quantity_committed")))
            requested_quantities = [Decimal(str(row.get("target_quantity"))) for row in rows]
            requested = sum(requested_quantities, Decimal(0))
        except (InvalidOperation, ValueError, TypeError):
            return False
        if committed != requested or any(quantity <= 0 for quantity in requested_quantities):
            return False
    return True


def _record_review(
    client: Any,
    demand_id: str,
    customer_id: str,
    reason: str,
) -> dict[str, Any]:
    result = client.rpc("set_demand_grouping_review_v1", {
        "p_demand_id": demand_id,
        "p_customer_id": customer_id,
        "p_reason": reason,
    }).execute()
    return _rpc_data(result)


def _persist_candidate_diagnostics(
    client: Any,
    demand_id: str,
    diagnostics: Sequence[Mapping[str, Any]],
) -> None:
    if diagnostics:
        evaluated_at = datetime.now(timezone.utc).isoformat()
        client.table("demand_grouping_candidate_diagnostics").upsert(
            [{**item, "evaluated_at": evaluated_at} for item in diagnostics],
            on_conflict="demand_id,candidate_group_id",
        ).execute()


async def try_group_new_demand(demand: Mapping[str, Any], client: Any) -> dict[str, Any]:
    """Use exact taxonomy aliases and mandatory compatibility before any group attach."""
    if demand.get("group_id") is not None or demand.get("grouping_status") == "grouped":
        return dict(demand)
    demand_id = str(demand.get("id") or "")
    customer_id = str(demand.get("customer_id") or "")
    if not demand_id or not customer_id:
        raise ValueError("demand_identity_missing")
    canonical = canonical_demand_family(
        str(demand.get("title") or ""),
        str(demand.get("category") or ""),
    )

    embedding_demand = {
        **demand,
        "product_name": demand.get("title") or demand.get("product_name"),
    }
    try:
        settings = get_settings()
        expected_hash = calculate_embedding_input_hash(
            build_embedding_input(embedding_demand),
            settings.OLLAMA_EMBEDDING_VERSION,
        )
        stored_embedding = get_demand_embedding(demand_id, client=client)
        if (
            stored_embedding is not None
            and stored_embedding.embedding_model == settings.OLLAMA_EMBEDDING_MODEL
            and stored_embedding.embedding_version == settings.OLLAMA_EMBEDDING_VERSION
            and stored_embedding.input_hash == expected_hash
        ):
            embedding = DemandEmbeddingResult(
                embedding=stored_embedding.embedding,
                embedding_model=stored_embedding.embedding_model,
                embedding_version=stored_embedding.embedding_version,
                input_hash=stored_embedding.input_hash,
            )
            embedding_is_stored = True
        else:
            embedding = await DemandEmbeddingService().generate(embedding_demand)
            embedding_is_stored = False
    except Exception as exc:
        logger.warning("Demand embedding read or generation failed (%s).", type(exc).__name__)
        return _record_review(client, demand_id, customer_id, "compatibility_assessment_failed")
    if not embedding_is_stored:
        try:
            persist_demand_embedding(demand_id, embedding, client=client)
        except Exception as exc:
            logger.warning("Demand embedding persistence failed (%s).", type(exc).__name__)
            return _record_review(client, demand_id, customer_id, "compatibility_assessment_failed")
    try:
        semantic_candidates = search_demand_embedding_candidates(
            embedding.embedding,
            embedding_model=embedding.embedding_model,
            embedding_version=embedding.embedding_version,
            exclude_demand_id=demand_id,
            client=client,
        )
    except Exception as exc:
        logger.warning("Semantic candidate search for demand %s failed (%s).", demand_id, type(exc).__name__)
        return _record_review(client, demand_id, customer_id, "compatibility_assessment_failed")
    if canonical is None:
        return dict(demand)

    normalized_rows = _rows(
        client.table("demand_normalized_requirements")
        .select("*")
        .eq("demand_id", demand_id)
        .eq("extraction_version", "v1")
        .limit(1)
        .execute()
    )
    if not normalized_rows:
        return _record_review(client, demand_id, customer_id, "normalized_requirements_missing")
    normalized_source = normalized_rows[0]
    demand_v2 = {
        **demand,
        "normalized_requirements": normalize_demand_v2(demand, normalized_source),
    }
    readiness = assess_demand_grouping_readiness(demand_v2)
    if readiness.status != "compatible":
        return _record_review(client, demand_id, customer_id, "candidate_compatibility_unresolved")

    candidate_distance_by_demand: dict[str, float] = {}
    for candidate in semantic_candidates:
        prior_distance = candidate_distance_by_demand.get(candidate.demand_id)
        if prior_distance is None or candidate.cosine_distance < prior_distance:
            candidate_distance_by_demand[candidate.demand_id] = candidate.cosine_distance

    try:
        family_groups = _rows(
            client.rpc("list_demand_group_family_candidates_v1", {
                "p_product_family_key": canonical.product_family_key,
                "p_category_key": canonical.category_key,
            }).execute()
        )
        family_group_ids = sorted(str(group["id"]) for group in family_groups)
        if len(family_group_ids) != len(set(family_group_ids)):
            raise RuntimeError("grouping_candidate_ids_duplicated")

        group_demands: list[Mapping[str, Any]] = []
        memberships: list[Mapping[str, Any]] = []
        normalized_by_id: dict[str, Mapping[str, Any]] = {demand_id: normalized_rows[0]}
        if family_group_ids:
            group_demands = _rows(
                client.table("demands")
                .select("*")
                .in_("group_id", family_group_ids)
                .execute()
            )
            memberships = _rows(
                client.table("demand_group_members")
                .select("*")
                .in_("group_id", family_group_ids)
                .execute()
            )
            member_ids = sorted({str(row["id"]) for row in group_demands if row.get("id")})
            if member_ids:
                normalized_group_rows = _rows(
                    client.table("demand_normalized_requirements")
                    .select("*")
                    .in_("demand_id", member_ids)
                    .eq("extraction_version", "v1")
                    .execute()
                )
                normalized_by_id.update({
                    str(row["demand_id"]): row
                    for row in normalized_group_rows
                    if row.get("demand_id")
                })
    except Exception as exc:
        logger.warning("Family candidate retrieval for demand %s failed (%s).", demand_id, type(exc).__name__)
        return _record_review(client, demand_id, customer_id, "candidate_compatibility_unresolved")

    candidate_group_distances: dict[str, float] = {}
    candidate_demands_by_group: dict[str, set[str]] = defaultdict(set)
    family_group_id_set = set(family_group_ids)
    for row in group_demands:
        candidate_demand_id = str(row.get("id") or "")
        demand_candidate_distance = candidate_distance_by_demand.get(candidate_demand_id)
        group_id = str(row.get("group_id") or "")
        if demand_candidate_distance is None or group_id not in family_group_id_set:
            continue
        candidate_demands_by_group[group_id].add(candidate_demand_id)
        previous_distance = candidate_group_distances.get(group_id)
        if previous_distance is None or demand_candidate_distance < previous_distance:
            candidate_group_distances[group_id] = demand_candidate_distance

    compatible_groups: list[tuple[Mapping[str, Any], float]] = []
    has_unresolved_candidate = False
    candidate_diagnostics: list[dict[str, Any]] = []
    for group in family_groups:
        group_id = str(group["id"])
        rows = [row for row in group_demands if str(row.get("group_id")) == group_id]
        eligibility_reasons = []
        if group.get("group_status") != "Proposed":
            eligibility_reasons.append("candidate_group_not_proposed")
        if group.get("status") != "Forming":
            eligibility_reasons.append("candidate_group_not_forming")
        if group.get("bidding_status") != "closed":
            eligibility_reasons.append("candidate_group_bidding_not_closed")
        if group.get("approved_at") is not None or group.get("approved_by") is not None:
            eligibility_reasons.append("candidate_group_approved")
        if group.get("offers_count") != 0:
            eligibility_reasons.append("candidate_group_has_offers")
        if not rows:
            eligibility_reasons.append("candidate_group_has_no_demands")
        elif not _group_membership_is_complete(group_id, rows, memberships):
            eligibility_reasons.append("candidate_group_membership_incomplete")
        if any(str(row.get("id")) not in normalized_by_id for row in rows):
            eligibility_reasons.append("candidate_normalized_requirements_missing")
        if eligibility_reasons:
            candidate_diagnostics.append({
                "demand_id": demand_id,
                "candidate_group_id": group_id,
                "candidate_demand_ids": sorted(candidate_demands_by_group[group_id]),
                "cosine_distance": candidate_group_distances.get(group_id),
                "assessment_status": "ineligible",
                "reason_codes": eligibility_reasons,
            })
            if candidate_demands_by_group[group_id]:
                has_unresolved_candidate = True
            continue
        try:
            records = [
                {
                    **row,
                    "normalized_requirements": normalize_demand_v2(
                        row,
                        normalized_by_id[str(row["id"])],
                    ),
                }
                for row in rows
            ]
            assessment = assess_group_compatibility([*records, demand_v2])
        except Exception as exc:
            logger.warning(
                "Group compatibility assessment for demand %s and group %s failed (%s).",
                demand_id,
                group_id,
                type(exc).__name__,
            )
            candidate_diagnostics.append({
                "demand_id": demand_id,
                "candidate_group_id": group_id,
                "candidate_demand_ids": sorted(candidate_demands_by_group[group_id]),
                "cosine_distance": candidate_group_distances.get(group_id),
                "assessment_status": "needs_review",
                "reason_codes": ["candidate_compatibility_assessment_failed"],
            })
            has_unresolved_candidate = True
            continue
        candidate_diagnostics.append({
            "demand_id": demand_id,
            "candidate_group_id": group_id,
            "candidate_demand_ids": sorted(candidate_demands_by_group[group_id]),
            "cosine_distance": candidate_group_distances.get(group_id),
            "assessment_status": assessment.status,
            "reason_codes": list(assessment.reasons),
        })
        if assessment.status == "compatible":
            compatible_groups.append((group, candidate_group_distances.get(group_id, float("inf"))))
        elif assessment.status not in {"needs_review", "rejected", "incompatible"}:
            has_unresolved_candidate = True

    try:
        _persist_candidate_diagnostics(client, demand_id, candidate_diagnostics)
    except Exception as exc:
        logger.warning("Candidate diagnostics persistence for demand %s failed (%s).", demand_id, type(exc).__name__)
        return _record_review(client, demand_id, customer_id, "candidate_compatibility_unresolved")

    if has_unresolved_candidate:
        return _record_review(client, demand_id, customer_id, "candidate_compatibility_unresolved")
    if compatible_groups:
        selected_group, _ = min(
            compatible_groups,
            key=lambda item: (
                item[1],
                str(item[0].get("created_at") or ""),
                str(item[0].get("id") or ""),
            ),
        )
        reason = None
    else:
        selected_group = None
        reason = "no_compatible_existing_group"

    snapshots = _snapshot(family_groups, group_demands, memberships, normalized_by_id)
    try:
        result = client.rpc("attach_demand_to_canonical_group_v1", {
            "p_demand_id": demand_id,
            "p_customer_id": customer_id,
            "p_target_group_id": str(selected_group["id"]) if selected_group else None,
            "p_product_family_key": canonical.product_family_key,
            "p_category_key": canonical.category_key,
            "p_expected_candidate_group_ids": family_group_ids,
            "p_candidate_snapshots": snapshots,
            "p_singleton_reason": reason,
        }).execute()
    except Exception:
        try:
            saved_rows = _rows(
                client.table("demands")
                .select("*")
                .eq("id", demand_id)
                .eq("customer_id", customer_id)
                .limit(1)
                .execute()
            )
            if saved_rows and saved_rows[0].get("group_id") is not None:
                return dict(saved_rows[0])
        except Exception:
            pass
        raise
    return _rpc_data(result)