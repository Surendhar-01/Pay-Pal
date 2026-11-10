import re
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.services import bidding_service
from tests.test_offer_contract import offer_payload


class RpcClient:
    def __init__(self, error=None, offer=None, demand=None):
        self.calls = []
        self.error = error
        self.offer = offer or {"id": "offer-1", "group_id": "canonical-group"}
        self.demand = demand or {"customer_id": "customer-1", "title": "reliable laptop"}
        self.notifications = []
        self.table_calls = []
        self.table_queries = []
        self.events = []

    def table(self, name):
        self.table_calls.append(name)
        return TableQuery(self, name)

    def rpc(self, name, parameters):
        self.calls.append((name, parameters))
        return self

    def execute(self):
        if self.error:
            raise self.error
        self.events.append("offer_saved")
        return SimpleNamespace(data=self.offer)


class TableQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = {}
        self.inserted = None

    def select(self, *_args):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, _limit):
        return self

    def insert(self, row):
        self.inserted = row
        return self

    def execute(self):
        self.client.events.append(f"{self.table}_query")
        self.client.table_queries.append({
            "table": self.table,
            "filters": dict(self.filters),
            "inserted": self.inserted,
        })
        if self.inserted is not None:
            self.client.notifications.append(self.inserted)
            self.client.events.append("notification_saved")
            return SimpleNamespace(data=[self.inserted])
        if self.table == "demands":
            if self.filters.get("id") == "demand-1":
                return SimpleNamespace(data=[self.client.demand])
            return SimpleNamespace(data=[])
        existing = [
            row for row in self.client.notifications
            if all(row.get(field) == value for field, value in self.filters.items())
        ]
        return SimpleNamespace(data=existing)


def test_submission_uses_one_atomic_rpc_and_database_resolves_redirect(monkeypatch):
    client = RpcClient()
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)
    result = bidding_service.submit_offer("authenticated-merchant", "redirected-group", offer_payload())
    assert result["group_id"] == "canonical-group"
    assert len(client.calls) == 1
    name, parameters = client.calls[0]
    assert name == "submit_merchant_offer_v1"
    assert parameters["p_group_id"] == "redirected-group"
    assert parameters["p_merchant_id"] == "authenticated-merchant"
    assert parameters["p_terms"] == {**offer_payload(), "min_order_quantity": "1"}
    assert re.fullmatch(r"OFF-[0-9A-F]{32}", parameters["p_offer_code"])


def test_direct_offer_notifies_only_the_exact_demand_owner_after_persistence(monkeypatch):
    client = RpcClient(
        offer={"id": "offer-1", "demand_id": "demand-1", "merchant_id": "merchant-1"},
        demand={"customer_id": "customer-owner", "title": "hp laptop"},
    )
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    result = bidding_service.submit_demand_offer("merchant-1", "demand-1", offer_payload())

    assert result["demand_id"] == "demand-1"
    assert len(client.notifications) == 1
    notification = client.notifications[0]
    assert notification["user_id"] == "customer-owner"
    assert notification["user_id"] != "merchant-1"
    assert notification["type"] == "offer"
    assert notification["title"] == "New product available for your demand"
    assert notification["description"] == (
        "A merchant has submitted a new product for your hp laptop demand. Review the offer details."
    )
    assert notification["action_url"] == "/customer/compare?demand=demand-1&offer=offer-1"
    assert client.events.index("offer_saved") < client.events.index("notification_saved")
    demand_lookup = next(query for query in client.table_queries if query["table"] == "demands")
    assert demand_lookup["filters"] == {"id": "demand-1"}


def test_submitting_merchant_is_not_notified_as_demand_owner(monkeypatch):
    client = RpcClient(
        offer={"id": "offer-1", "demand_id": "demand-1", "merchant_id": "merchant-1"},
        demand={"customer_id": "merchant-1", "title": "hp laptop"},
    )
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    bidding_service.submit_demand_offer("merchant-1", "demand-1", offer_payload())

    assert client.notifications == []


def test_failed_offer_submission_does_not_create_notification(monkeypatch):
    client = RpcClient(APIError({"code": "P0001", "message": "OFFER_INVALID_TERMS", "details": None, "hint": None}))
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    with pytest.raises(ValueError):
        bidding_service.submit_demand_offer("merchant-1", "demand-1", offer_payload())

    assert client.notifications == []
    assert client.table_calls == []


def test_duplicate_offer_retry_does_not_create_another_notification(monkeypatch):
    client = RpcClient(APIError({"code": "P0001", "message": "OFFER_DUPLICATE", "details": None, "hint": None}))
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    with pytest.raises(bidding_service.OfferConflict):
        bidding_service.submit_demand_offer("merchant-1", "demand-1", offer_payload())

    assert client.notifications == []
    assert client.table_calls == []


def test_notification_action_url_deduplicates_repeated_notification_work(monkeypatch):
    client = RpcClient(
        offer={"id": "offer-1", "demand_id": "demand-1", "merchant_id": "merchant-1"},
        demand={"customer_id": "customer-owner", "title": "hp laptop"},
    )
    client.notifications.append({
        "id": "notification-existing",
        "user_id": "customer-owner",
        "type": "offer",
        "action_url": "/customer/compare?demand=demand-1&offer=offer-1",
    })
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    bidding_service.submit_demand_offer("merchant-1", "demand-1", offer_payload())

    assert len(client.notifications) == 1
    assert not any(query["inserted"] for query in client.table_queries)


def test_group_offer_without_exact_demand_id_does_not_notify_group_members(monkeypatch):
    client = RpcClient()
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    bidding_service.submit_offer("merchant-1", "group-1", offer_payload())

    assert client.notifications == []
    assert client.table_calls == []


def test_revision_does_not_create_new_product_notification(monkeypatch):
    client = RpcClient()
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)

    bidding_service.revise_offer("merchant-1", "offer-1", {
        "unit_price": "18.00",
        "expected_unit_price": "19.95",
    })

    assert client.notifications == []
    assert client.table_calls == []


def test_offer_codes_are_not_group_merchant_prefixes(monkeypatch):
    client = RpcClient()
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)
    for _ in range(2):
        bidding_service.submit_offer("same-merchant", "same-group", offer_payload())
    assert client.calls[0][1]["p_offer_code"] != client.calls[1][1]["p_offer_code"]


@pytest.mark.parametrize("code,message,error", [
    ("PGRST202", "missing RPC", RuntimeError),
    ("42703", "missing group_id", RuntimeError),
    ("42501", "denied", RuntimeError),
    ("P0001", "OFFER_DUPLICATE", bidding_service.OfferConflict),
    ("23505", "constraint details", bidding_service.OfferConflict),
    ("P0001", "OFFER_STALE_PRICE", bidding_service.OfferConflict),
    ("P0001", "OFFER_GROUP_CLOSED", bidding_service.OfferConflict),
    ("P0001", "OFFER_LEGACY_QUANTITY", bidding_service.OfferConflict),
    ("P0001", "OFFER_INVALID_TERMS", ValueError),
])
def test_rpc_errors_fail_closed_without_rest_fallback(monkeypatch, code, message, error):
    client = RpcClient(APIError({"code": code, "message": message, "details": None, "hint": None}))
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)
    with pytest.raises(error):
        bidding_service.submit_offer("merchant", "group", offer_payload())
    assert len(client.calls) == 1


def test_revision_uses_authenticated_owner_and_expected_price(monkeypatch):
    client = RpcClient()
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: client)
    bidding_service.revise_offer("merchant", "offer", {"unit_price": "18.00", "expected_unit_price": "19.95"})
    assert client.calls == [("revise_merchant_offer_v1", {
        "p_merchant_id": "merchant", "p_offer_id": "offer",
        "p_unit_price": "18.00", "p_expected_unit_price": "19.95",
    })]


def test_unconfigured_service_client_fails_closed(monkeypatch):
    monkeypatch.setattr(bidding_service, "get_supabase_service_client", lambda: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        bidding_service.submit_offer("merchant", "group", offer_payload())
