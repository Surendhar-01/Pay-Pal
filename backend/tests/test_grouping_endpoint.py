import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import grouping as grouping_endpoint
from app.api.v1.endpoints.grouping import generate_demand_groups, retry_reviewed_demand_grouping


@pytest.mark.anyio
async def test_legacy_admin_generation_endpoint_is_disabled():
    with pytest.raises(HTTPException) as exc_info:
        await generate_demand_groups(current_user={"id": "admin-1"})

    assert exc_info.value.status_code == 503
    assert "generation is disabled" in exc_info.value.detail


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, _limit):
        return self

    def execute(self):
        rows = self.client.rows.get(self.table, [])
        return _Result([
            row for row in rows
            if all(row.get(column) == value for column, value in self.filters.items())
        ])


class _Client:
    def __init__(self):
        self.rows = {
            "demands": [{
                "id": "older-headphone-demand",
                "customer_id": "customer-1",
                "group_id": None,
                "grouping_status": "needs_review",
                "status": "Submitted",
                "title": "wireless headphones",
                "category": "Electronics > Audio > Wireless Headphones",
            }],
            "demand_normalized_requirements": [{
                "demand_id": "older-headphone-demand",
                "extraction_version": "v1",
            }],
            "demand_grouping_candidate_diagnostics": [],
        }
        self.rows.update({
            table: [] for table in (
                "demand_group_members", "merchant_offers", "customer_offer_selections",
                "customer_offer_eligibility", "offer_comparison_history", "orders",
            )
        })

    def table(self, name):
        return _Query(self, name)


@pytest.mark.anyio
async def test_retry_uses_the_existing_reviewed_demand_and_is_not_creation():
    client = _Client()
    diagnostic = {
        "demand_id": "older-headphone-demand",
        "candidate_group_id": "group-1",
        "candidate_demand_ids": ["candidate-demand"],
        "cosine_distance": 0.1,
        "assessment_status": "compatible",
        "reason_codes": [],
        "evaluated_at": "2026-01-01T00:00:00Z",
    }
    client.rows["demand_grouping_candidate_diagnostics"] = [diagnostic]
    calls = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(grouping_endpoint, "get_supabase_client", lambda: client)

    async def group_existing(demand, passed_client):
        calls.append((demand["id"], passed_client))
        return {**demand, "group_id": "group-1", "grouping_status": "grouped"}

    monkeypatch.setattr(grouping_endpoint, "try_group_new_demand", group_existing)
    try:
        result = await retry_reviewed_demand_grouping(
            "older-headphone-demand",
            current_user={"id": "admin-1", "role": "admin"},
        )
    finally:
        monkeypatch.undo()

    assert result["demand"]["group_id"] == "group-1"
    assert result["candidate_diagnostics"] == [diagnostic]
    assert calls == [("older-headphone-demand", client)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "table",
    [
        "demand_group_members",
        "merchant_offers",
        "customer_offer_selections",
        "customer_offer_eligibility",
        "offer_comparison_history",
        "orders",
    ],
)
async def test_retry_refuses_downstream_records_and_grouped_demands(monkeypatch, table):
    client = _Client()
    monkeypatch.setattr(grouping_endpoint, "get_supabase_client", lambda: client)
    client.rows[table] = [{"id": "related-1", "demand_id": "older-headphone-demand"}]

    with pytest.raises(HTTPException) as downstream_error:
        await retry_reviewed_demand_grouping(
            "older-headphone-demand",
            current_user={"id": "admin-1", "role": "admin"},
        )
    assert downstream_error.value.status_code == 409

    client.rows[table] = []
    client.rows["demands"][0].update({"group_id": "group-1", "grouping_status": "grouped"})
    with pytest.raises(HTTPException) as grouped_error:
        await retry_reviewed_demand_grouping(
            "older-headphone-demand",
            current_user={"id": "admin-1", "role": "admin"},
        )
    assert grouped_error.value.status_code == 409
