import pytest

from app.services import demand_service
from app.services import demand_group_submission_service


class Result:
    def __init__(self, data):
        self.data = data
        self.count = len(data) if isinstance(data, list) else None


class Query:
    def __init__(self, table_name, records):
        self.table_name = table_name
        self.records = records

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return Result(self.records.get(self.table_name, []))


class Client:
    def __init__(self, records, rpc_error=None):
        self.records = records
        self.rpc_call = None
        self.rpc_calls = []
        self.rpc_error = rpc_error
        self.table_calls = []

    def table(self, table_name):
        self.table_calls.append(table_name)
        return Query(table_name, self.records)

    def rpc(self, function_name, params):
        self.rpc_call = (function_name, params)
        self.rpc_calls.append(self.rpc_call)
        if self.rpc_error is not None:
            raise self.rpc_error
        return QueryResult(self.records["updated_demand"])


class PostgrestError(Exception):
    def __init__(self, code, message, details=None, hint=None, status_code=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.hint = hint
        self.status_code = status_code


class QueryResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return Result(self.data)


class AnalysisResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class AnalysisQuery:
    def __init__(self, table_name, current, records, normalized, page_limit):
        self.table_name = table_name
        self.current = current
        self.records = records
        self.normalized = normalized
        self.page_limit = page_limit
        self.page_range = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self.page_range = (start, end)
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.table_name == "demand_normalized_requirements":
            return AnalysisResult(self.normalized)
        if self.page_range is None:
            return AnalysisResult([self.current])
        start, end = self.page_range
        page_end = min(end + 1, start + self.page_limit)
        return AnalysisResult(self.records[start:page_end], len(self.records))


class AnalysisClient:
    def __init__(self, current, records, normalized=None, page_limit=500):
        self.current = current
        self.records = records
        self.normalized = normalized or []
        self.page_limit = page_limit

    def table(self, table_name):
        return AnalysisQuery(
            table_name,
            self.current,
            self.records,
            self.normalized,
            self.page_limit,
        )


def test_confirmed_requirement_edit_uses_owner_scoped_atomic_rpc(monkeypatch):
    existing = {
        "id": "demand-1",
        "customer_id": "customer-1",
        "demand_code": "MT-REFERENCE",
        "title": "Old tank",
        "category": "Industrial",
        "target_quantity": 1,
        "unit": "unit",
        "min_target_unit_price": 100,
        "max_target_unit_price": 200,
        "currency": "INR",
        "status": "Submitted",
        "delivery_address": {"city": "Pune"},
        "technical_specifications": [
            {"name": "Material", "value": "SS 304", "mandatory": True}
        ],
    }
    normalized = {
        "normalized_material": "SS 304",
        "mandatory_requirements": [{"name": "Material", "value": "SS 304"}],
        "optional_preferences": [],
        "delivery_constraints": {"required_delivery_date": "2026-12-01"},
    }
    saved = {**existing, "title": "Updated tank", "target_quantity": 1.25}
    client = Client(
        {
            "demands": [existing],
            "demand_normalized_requirements": [normalized],
            "updated_demand": saved,
        }
    )
    monkeypatch.setattr(demand_service, "_client", lambda: client)

    result = demand_service.update_demand(
        "customer-1",
        "demand-1",
        {
            "product_name": "Updated tank",
            "category": "Industrial",
            "quantity": 1.25,
            "unit": "units",
            "minimum_target_unit_price": 110,
            "maximum_target_unit_price": 220,
            "currency": "INR",
            "mandatory_requirements": {"Material": "SS 316"},
            "optional_preferences": {"Brand": "Acme"},
            "technical_specifications": [
                {"name": "Material", "value": "SS 316", "mandatory": True},
                {"name": "Brand", "value": "Acme", "mandatory": False},
            ],
        },
    )

    assert result["id"] == "demand-1"
    assert client.rpc_call is not None
    function_name, params = client.rpc_call
    assert function_name == "update_customer_demand_v1"
    assert params["p_customer_id"] == "customer-1"
    assert params["p_demand_id"] == "demand-1"
    assert params["p_demand"]["title"] == "Updated tank"
    assert params["p_demand"]["target_quantity"] == 1.25
    assert params["p_demand"]["delivery_address"] == {"city": "Pune"}
    assert params["p_demand"]["normalized_requirements"]["mandatory_requirements"] == [
        {"name": "Material", "value": "SS 316"}
    ]


@pytest.mark.anyio
async def test_create_demand_persists_owner_and_requirement_maps_atomically(monkeypatch):
    saved = {
        "id": "demand-1",
        "customer_id": "customer-1",
        "demand_code": "MT-REFERENCE",
    }
    client = Client({"updated_demand": saved})
    monkeypatch.setattr(demand_service, "get_supabase_service_client", lambda: client)

    result = await demand_service.create_demand(
        "customer-1",
        {
            "product_name": "Wireless headphones",
            "category": "Electronics",
            "quantity": 2,
            "unit": "units",
            "minimum_target_unit_price": 1000,
            "maximum_target_unit_price": 2000,
            "currency": "INR",
            "mandatory_requirements": {"noise cancellation": "Active"},
            "optional_preferences": {"colour": "Black"},
        },
        "submission-1",
    )

    assert result == saved
    assert client.rpc_call is not None
    function_name, params = client.rpc_call
    assert function_name == "create_customer_demand_v2"
    assert params["p_submission_id"] == "submission-1"
    assert params["p_demand"]["customer_id"] == "customer-1"
    assert set(params) == {"p_submission_id", "p_demand"}
    assert params["p_demand"]["submission_id"] == "submission-1"
    assert params["p_demand"]["target_quantity"] == 2
    assert params["p_demand"]["currency"] == "INR"
    assert params["p_demand"]["canonical_product_family_key"] is None
    assert params["p_demand"]["canonical_category_key"] is None
    normalized = params["p_demand"]["normalized_requirements"]
    assert normalized["product_family_key"] == "wireless headphones"
    assert normalized["mandatory_requirements"] == [
        {"name": "noise cancellation", "value": "Active"}
    ]
    assert normalized["optional_preferences"] == [
        {"name": "colour", "value": "Black"}
    ]


@pytest.mark.anyio
async def test_retry_of_already_grouped_submission_skips_regrouping(monkeypatch):
    saved = {
        "id": "demand-1",
        "customer_id": "customer-1",
        "demand_code": "MT-REFERENCE",
        "group_id": "group-1",
        "grouping_status": "grouped",
    }
    client = Client({"updated_demand": saved})
    monkeypatch.setattr(demand_service, "get_supabase_service_client", lambda: client)
    monkeypatch.setattr(
        demand_group_submission_service,
        "DemandEmbeddingService",
        lambda: pytest.fail("A grouped submission retry must not regenerate an embedding."),
    )

    result = await demand_service.create_demand(
        "customer-1",
        {
            "product_name": "Wireless headphones",
            "category": "Electronics",
            "quantity": 2,
            "unit": "units",
            "minimum_target_unit_price": 1000,
            "maximum_target_unit_price": 2000,
            "currency": "INR",
        },
        "submission-1",
    )

    assert result == saved
    assert client.rpc_call[0] == "create_customer_demand_v2"


@pytest.mark.anyio
async def test_create_demand_requires_service_role_client_without_anon_fallback(monkeypatch):
    monkeypatch.setattr(demand_service, "get_supabase_service_client", lambda: None)

    with pytest.raises(demand_service.DemandPersistenceError) as error:
        await demand_service.create_demand(
            "customer-1",
            {
                "product_name": "Test",
                "category": "Electronics",
                "quantity": 1,
                "unit": "unit",
                "minimum_target_unit_price": 10,
                "maximum_target_unit_price": 20,
                "currency": "INR",
            },
            "submission-1",
        )

    assert error.value.diagnostics["code"] == "SERVICE_ROLE_CLIENT_UNAVAILABLE"
    assert "service-role" in error.value.public_message


@pytest.mark.anyio
@pytest.mark.parametrize(
    "postgrest_error,expected_code,expected_detail",
    [
        (
            PostgrestError(
                "PGRST202",
                "Could not find function public.create_customer_demand_v2 in the schema cache",
                "No matching function was found",
                "Check the function signature",
                404,
            ),
            "PGRST202",
            "not exposed to PostgREST",
        ),
        (
            PostgrestError("XX404", "Unexpected resource response", status_code=404),
            "XX404",
            "unexpected database 404",
        ),
        (
            PostgrestError("42501", "permission denied for function", status_code=403),
            "42501",
            "blocked by database permissions",
        ),
    ],
)
async def test_create_demand_classifies_rpc_errors_without_table_insert_fallback(
    monkeypatch,
    postgrest_error,
    expected_code,
    expected_detail,
):
    client = Client({}, rpc_error=postgrest_error)
    monkeypatch.setattr(demand_service, "get_supabase_service_client", lambda: client)

    with pytest.raises(demand_service.DemandPersistenceError) as error:
        await demand_service.create_demand(
            "customer-1",
            {
                "product_name": "Test product",
                "category": "Electronics",
                "quantity": 1,
                "unit": "unit",
                "minimum_target_unit_price": 10,
                "maximum_target_unit_price": 20,
                "currency": "INR",
            },
            "submission-1",
        )

    assert error.value.diagnostics["code"] == expected_code
    assert expected_detail in error.value.public_message
    assert len(client.rpc_calls) == 1
    assert client.table_calls == []


def test_postgrest_diagnostics_redact_personal_and_secret_values():
    error = PostgrestError(
        "PGRST202",
        "Failure for customer@example.com id 123e4567-e89b-12d3-a456-426614174000 password=topsecret",
        hint="Bearer very-secret-token",
    )

    diagnostics = demand_service._postgrest_diagnostics(error)
    serialized = str(diagnostics)

    assert diagnostics["code"] == "PGRST202"
    assert "customer@example.com" not in serialized
    assert "123e4567-e89b-12d3-a456-426614174000" not in serialized
    assert "topsecret" not in serialized
    assert "very-secret-token" not in serialized


@pytest.mark.anyio
async def test_replaying_same_submission_id_reuses_rpc_idempotency_key(monkeypatch):
    saved = {
        "id": "demand-1",
        "customer_id": "customer-1",
        "demand_code": "MT-REFERENCE",
        "group_id": "group-1",
        "grouping_status": "grouped",
    }
    client = Client({"updated_demand": saved})
    monkeypatch.setattr(demand_service, "get_supabase_service_client", lambda: client)

    for _ in range(2):
        result = await demand_service.create_demand(
            "customer-1",
            {
                "product_name": "Test",
                "category": "Electronics",
                "quantity": 1,
                "unit": "unit",
                "minimum_target_unit_price": 10,
                "maximum_target_unit_price": 20,
                "currency": "INR",
            },
            "same-submission-id",
        )
        assert result == saved

    assert len(client.rpc_calls) == 2
    assert [call[1]["p_submission_id"] for call in client.rpc_calls] == [
        "same-submission-id",
        "same-submission-id",
    ]


def test_list_demands_scopes_results_to_authenticated_customer(monkeypatch):
    class OwnerScopedQuery:
        def __init__(self):
            self.filters = []
            self.selection = None
            self.select_options = None
            self.ordering = None
            self.page_range = None

        def select(self, selection, **kwargs):
            self.selection = selection
            self.select_options = kwargs
            return self

        def eq(self, column, value):
            self.filters.append((column, value))
            return self

        def order(self, column, desc=False):
            self.ordering = (column, desc)
            return self

        def range(self, start, end):
            self.page_range = (start, end)
            return self

        def execute(self):
            return Result([
                {
                    "id": "demand-1",
                    "customer_id": "customer-1",
                    "group_id": "group-1",
                    "demand_groups": {
                        "group_reference": "GRP-123",
                        "participating_demand_count": 4,
                        "participants_count": 3,
                    },
                },
                {"id": "demand-2", "customer_id": "customer-1", "group_id": None, "demand_groups": None},
            ])

    class OwnerScopedClient:
        def __init__(self):
            self.query = OwnerScopedQuery()

        def table(self, _table_name):
            return self.query

    client = OwnerScopedClient()
    monkeypatch.setattr(demand_service, "_client", lambda: client)

    items, total = demand_service.list_demands("customer-1", page=2, page_size=2)

    assert total == 2
    assert items[0]["customer_id"] == "customer-1"
    assert items[0]["demand_groups"]["group_reference"] == "GRP-123"
    assert items[1]["group_id"] is None
    assert items[1]["demand_groups"] is None
    assert client.query.filters == [("customer_id", "customer-1")]
    assert client.query.selection == "*,demand_groups!demands_group_id_fkey(group_reference,participating_demand_count,participants_count)"
    assert client.query.select_options == {"count": "exact"}
    assert client.query.ordering == ("created_at", True)
    assert client.query.page_range == (2, 3)


def test_analysis_fetches_all_pages_and_rejects_truncated_pages(monkeypatch):
    current = {
        "id": "current",
        "customer_id": "customer-1",
        "title": "Tank",
        "category": "Industrial",
    }
    records = [
        {
            "id": f"demand-{index}",
            "customer_id": f"customer-{index}",
            "title": "Tank",
            "category": "Industrial",
            "demand_normalized_requirements": [],
        }
        for index in range(501)
    ]
    monkeypatch.setattr(
        demand_service,
        "_client",
        lambda: AnalysisClient(current, records),
    )

    _, fetched = demand_service.get_analysis_dataset("customer-1", "current")

    assert len(fetched) == 501
    assert "customer_id" in fetched[0]
    monkeypatch.setattr(
        demand_service,
        "_client",
        lambda: AnalysisClient(current, records, page_limit=100),
    )
    with pytest.raises(RuntimeError, match="incomplete dataset"):
        demand_service.get_analysis_dataset("customer-1", "current")
