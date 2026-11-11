import logging
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints import demands as demands_endpoint
from app.main import app
from app.services.demand_service import DemandPersistenceError


def test_create_demand_requires_auth():
    client = TestClient(app)
    response = client.post(
        "/api/v1/customer/demands",
        json={
            "product_name": "Test",
            "category": "Electronics",
            "quantity": 1,
            "unit": "units",
            "minimum_target_unit_price": 10,
            "maximum_target_unit_price": 20,
        },
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_demand_rpc_failure_returns_safe_action_and_logs_structured_diagnostics(monkeypatch, caplog):
    monkeypatch.setattr(
        demands_endpoint,
        "get_settings",
        lambda: SimpleNamespace(DEMAND_PERSISTENCE_ENABLED=True),
    )

    async def fail_persistence(*_args):
        raise DemandPersistenceError(
            "Demand submission is unavailable because its database RPC is not exposed to PostgREST.",
            {
                "code": "PGRST202",
                "message": "Could not find function in the schema cache",
                "details": None,
                "hint": "Verify the migration and schema cache",
                "http_status": "404",
            },
        )

    monkeypatch.setattr(demands_endpoint, "create_demand", fail_persistence)

    class Payload:
        def model_dump(self, **_kwargs):
            return {"product_name": "private customer detail"}

    with caplog.at_level(logging.ERROR, logger="markettwin.api.demands"):
        with pytest.raises(HTTPException) as error:
            await demands_endpoint.create_customer_demand(
                payload=Payload(),
                current_user={"id": "customer-1"},
                idempotency_key=UUID("1e3477ec-d88c-4010-9db4-f20260dfcc68"),
            )

    assert error.value.status_code == 503
    assert "not exposed to PostgREST" in error.value.detail
    assert "PGRST202" not in error.value.detail
    assert "PGRST202" in caplog.text
    assert "private customer detail" not in caplog.text


def test_list_demands_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/customer/demands")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_customer_demand_list_maps_grouped_and_ungrouped_rows(monkeypatch):
    calls = []
    rows = [
        {
            "id": "demand-1",
            "customer_id": "customer-1",
            "title": "Grouped laptop",
            "target_quantity": 2,
            "group_id": "group-1",
            "demand_groups": {
                "group_reference": "GRP-123",
                "participating_demand_count": 4,
                "participants_count": 3,
            },
        },
        {
            "id": "demand-2",
            "customer_id": "customer-1",
            "title": "Standalone laptop",
            "target_quantity": 1,
            "group_id": None,
            "demand_groups": None,
        },
    ]

    def list_customer_rows(customer_id, **kwargs):
        calls.append((customer_id, kwargs))
        return rows, len(rows)

    monkeypatch.setattr(demands_endpoint, "list_demands", list_customer_rows)

    result = await demands_endpoint.get_customer_demands(
        page=2,
        page_size=1,
        status_filter=None,
        search=None,
        current_user={"id": "customer-1"},
    )

    assert calls == [("customer-1", {"page": 2, "page_size": 1, "status": None, "search": None})]
    assert result.total == 2
    assert result.page == 2
    assert result.page_size == 1
    assert result.items[0].group_reference == "GRP-123"
    assert result.items[0].participating_demand_count == 4
    assert result.items[0].participants_count == 3
    assert result.items[1].group_id is None
    assert result.items[1].group_reference is None
    assert result.items[1].participating_demand_count is None
    assert result.items[1].participants_count is None


@pytest.mark.anyio
async def test_customer_demand_list_keeps_service_failures_as_503(monkeypatch):
    def fail_list(*_args, **_kwargs):
        raise RuntimeError("PostgREST relationship query failed")

    monkeypatch.setattr(demands_endpoint, "list_demands", fail_list)

    with pytest.raises(HTTPException) as error:
        await demands_endpoint.get_customer_demands(
            current_user={"id": "customer-1"},
        )

    assert error.value.status_code == 503
    assert error.value.detail == "Your demands are temporarily unavailable."