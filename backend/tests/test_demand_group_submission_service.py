import pytest

from app.services import demand_group_submission_service as grouping_service
from app.services.demand_compatibility import CompatibilityAssessment
from app.services.demand_embedding_service import (
    EMBEDDING_DIMENSIONS,
    DemandEmbeddingCandidate,
    DemandEmbeddingResult,
    StoredDemandEmbedding,
    build_embedding_input,
    calculate_embedding_input_hash,
)
from app.services.demand_group_submission_service import try_group_new_demand


class _EmbeddingHarness:
    def __init__(self):
        self.candidates = [DemandEmbeddingCandidate("existing-demand", 0.1, 0.9)]
        self.generate_error = None
        self.persist_error = None
        self.search_error = None
        self.read_calls = []
        self.events = []
        self.generate_calls = []
        self.persist_calls = []
        self.search_calls = []

    async def generate(self, demand):
        self.events.append("generate")
        self.generate_calls.append(demand)
        if self.generate_error:
            raise self.generate_error
        return DemandEmbeddingResult(
            embedding=[0.25] * EMBEDDING_DIMENSIONS,
            embedding_model="nomic-embed-text",
            embedding_version="v1",
            input_hash="hash-a",
        )

    def persist(self, demand_id, embedding, client=None):
        self.events.append("persist")
        self.persist_calls.append((demand_id, embedding, client))
        if self.persist_error:
            raise self.persist_error
        return "saved"

    def search(self, query_embedding, **kwargs):
        self.events.append("search")
        self.search_calls.append((query_embedding, kwargs))
        if self.search_error:
            raise self.search_error
        return self.candidates


@pytest.fixture
def embeddings(monkeypatch):
    harness = _EmbeddingHarness()
    monkeypatch.setattr(grouping_service, "DemandEmbeddingService", lambda: harness)
    monkeypatch.setattr(
        grouping_service,
        "get_demand_embedding",
        lambda demand_id, client=None: (
            harness.read_calls.append(demand_id),
            harness.events.append("read"),
            None,
        )[-1],
    )
    monkeypatch.setattr(grouping_service, "persist_demand_embedding", harness.persist)
    monkeypatch.setattr(grouping_service, "search_demand_embedding_candidates", harness.search)
    return harness


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

    def in_(self, column, values):
        self.filters[column] = list(values)
        return self

    def limit(self, _limit):
        return self

    def upsert(self, values, on_conflict=None):
        self.client.upserts.append((self.table, values, on_conflict))
        return self

    def execute(self):
        if self.client.fail_read and self.client.fail_read(self.table, self.filters):
            raise RuntimeError("candidate member read unavailable")
        if self.table == "demand_grouping_candidate_diagnostics" and self.client.fail_diagnostics_write:
            raise RuntimeError("diagnostics unavailable")
        if self.table == "demand_grouping_candidate_diagnostics" and self.table not in self.client.rows:
            return _Result([])
        rows = self.client.rows.get(self.table, [])
        return _Result([
            row for row in rows
            if all(
                row.get(column) in value if isinstance(value, list) else row.get(column) == value
                for column, value in self.filters.items()
            )
        ])


class _Rpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return _Result(self.data)


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.rpc_calls = []
        self.upserts = []
        self.fail_family_candidate_read = False
        self.fail_diagnostics_write = False
        self.fail_attach = False
        self.fail_read = None

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name == "list_demand_group_family_candidates_v1":
            if self.fail_family_candidate_read:
                raise RuntimeError("candidate list unavailable")
            return _Rpc(self.rows.get("demand_groups", []))
        if name == "attach_demand_to_canonical_group_v1":
            if self.fail_attach:
                raise RuntimeError("canonical attachment unavailable")
            return _Rpc({
                "id": "new-demand",
                "group_id": params["p_target_group_id"] or "new-group",
                "grouping_status": "grouped",
            })
        return _Rpc({
            "id": "new-demand",
            "group_id": None,
            "grouping_status": "needs_review",
        })


def _demand(demand_id, group_id, customer_id, title, quantity, created_at):
    return {
        "id": demand_id,
        "group_id": group_id,
        "customer_id": customer_id,
        "title": title,
        "category": "Computers",
        "description": None,
        "technical_specifications": [],
        "target_quantity": quantity,
        "unit": "units",
        "created_at": created_at,
        "updated_at": created_at,
        "status": "Submitted",
    }


def _normalized(demand_id, title):
    return {
        "demand_id": demand_id,
        "extraction_version": "v1",
        "product_type": title,
        "normalized_product_name": title,
        "normalized_category": "Computers",
        "mandatory_requirements": [],
        "optional_preferences": [],
        "commercial_constraints": {
            "minimum_target_unit_price": 100,
            "maximum_target_unit_price": 200,
            "currency": "USD",
            "unit": "units",
        },
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _candidate_client(candidate_budget=(100, 200), unknown=False):
    existing = _demand(
        "existing-demand",
        "group-1",
        "customer-old",
        "reliable laptop",
        2,
        "2026-01-01T00:00:00Z",
    )
    existing_normalized = _normalized(existing["id"], existing["title"])
    existing_normalized["commercial_constraints"].update({
        "minimum_target_unit_price": candidate_budget[0],
        "maximum_target_unit_price": candidate_budget[1],
    })
    if unknown:
        existing_normalized["mandatory_requirements"] = [
            {"name": "unrecognized_constraint", "value": "opaque"},
        ]
    group = {
        "id": "group-1",
        "product_title": "reliable laptop",
        "category": "Computers",
        "group_status": "Proposed",
        "status": "Forming",
        "bidding_status": "closed",
        "approved_at": None,
        "approved_by": None,
        "offers_count": 0,
        "created_at": "2026-01-01T00:00:00Z",
    }
    member = {
        "group_id": "group-1",
        "customer_id": "customer-old",
        "demand_id": "existing-demand",
        "quantity_committed": 2,
        "membership_status": "active",
        "removed_at": None,
    }
    new_demand = _demand(
        "new-demand",
        None,
        "customer-new",
        "laptop suitable",
        3,
        "2026-01-02T00:00:00Z",
    )
    rows = {
        "demand_normalized_requirements": [
            _normalized("new-demand", "laptop suitable"),
            existing_normalized,
        ],
        "demand_groups": [group],
        "demand_group_redirects": [],
        "demands": [existing],
        "demand_group_members": [member],
    }
    return _Client(rows), new_demand


def _headphone_candidate_client():
    client, demand = _candidate_client()
    category = "Electronics → Audio → Wireless Headphones"
    demand.update({
        "title": "wireless over-ear headphones",
        "category": category,
        "target_quantity": 2,
        "grouping_status": "needs_review",
        "grouping_review_reason": "unrecognized_product_category_alias",
    })
    existing = client.rows["demands"][0]
    existing.update({"title": demand["title"], "category": "Electronics"})
    group = client.rows["demand_groups"][0]
    group.update({"product_title": demand["title"], "category": "Electronics"})
    for normalized in client.rows["demand_normalized_requirements"]:
        normalized.update({
            "product_type": demand["title"],
            "normalized_product_name": demand["title"],
            "normalized_category": category if normalized["demand_id"] == "new-demand" else "Electronics",
            "commercial_constraints": {
                "minimum_target_unit_price": 2500,
                "maximum_target_unit_price": 6000,
                "currency": "INR",
            },
        })
        normalized["mandatory_requirements"] = (
            _latest_headphone_requirements()
            if normalized["demand_id"] == "new-demand"
            else _older_headphone_requirements()
        )
        normalized["optional_preferences"] = [
            {"name": "multipoint_connectivity", "value": "preferred"},
            {"name": "design_style", "value": "foldable"},
        ]
    return client, demand


def _older_headphone_requirements():
    return [
        {"name": "noise_cancellation", "value": "Active Noise Cancellation"},
        {"name": "bluetooth_version", "value": "Bluetooth 5.2 or newer"},
        {"name": "battery_life", "value": "at least 40 hours"},
        {"name": "microphone", "value": "clear built-in microphone"},
        {"name": "charging_port", "value": "USB Type-C"},
        {"name": "application", "value": "wireless over-ear headphones for music listening"},
    ]


def _latest_headphone_requirements():
    return [
        {"name": "noise_cancellation", "value": "Active Noise Cancellation"},
        {"name": "bluetooth_version", "value": "Bluetooth 5.2 or newer"},
        {"name": "battery_life", "value": "at least 30 hours"},
        {"name": "microphone", "value": "clear built-in microphone"},
        {"name": "charging_port", "value": "USB Type-C"},
        {"name": "active_noise_cancellation", "value": "required"},
        {"name": "bluetooth_5_2_or_newer", "value": "required"},
        {"name": "clear_built_in_microphone", "value": "required"},
        {"name": "usb_type_c_charging", "value": "required"},
        {"name": "good_bass", "value": "required"},
        {"name": "application", "value": "wireless over-ear headphones for work and entertainment"},
    ]


def _append_candidate_group(client, group_id, budget=(100, 200), created_at="2026-01-03T00:00:00Z"):
    demand_id = f"{group_id}-demand"
    customer_id = f"{group_id}-customer"
    existing = _demand(
        demand_id,
        group_id,
        customer_id,
        "reliable laptop",
        1,
        created_at,
    )
    normalized = _normalized(demand_id, existing["title"])
    normalized["commercial_constraints"].update({
        "minimum_target_unit_price": budget[0],
        "maximum_target_unit_price": budget[1],
    })
    client.rows["demand_groups"].append({
        "id": group_id,
        "product_title": "reliable laptop",
        "category": "Computers",
        "group_status": "Proposed",
        "status": "Forming",
        "bidding_status": "closed",
        "approved_at": None,
        "approved_by": None,
        "offers_count": 0,
        "created_at": created_at,
    })
    client.rows["demands"].append(existing)
    client.rows["demand_normalized_requirements"].append(normalized)
    client.rows["demand_group_members"].append({
        "group_id": group_id,
        "customer_id": customer_id,
        "demand_id": demand_id,
        "quantity_committed": 1,
        "membership_status": "active",
        "removed_at": None,
    })
    return demand_id


@pytest.mark.anyio
async def test_compatible_candidate_is_attached_with_snapshot(embeddings):
    client, demand = _candidate_client()

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    name, params = next(
        (name, params) for name, params in client.rpc_calls
        if name == "attach_demand_to_canonical_group_v1"
    )
    assert name == "attach_demand_to_canonical_group_v1"
    assert params["p_target_group_id"] == "group-1"
    assert params["p_product_family_key"] == "laptop"
    assert params["p_expected_candidate_group_ids"] == ["group-1"]
    assert params["p_candidate_snapshots"][0]["demand_ids"] == ["existing-demand"]
    assert params["p_candidate_snapshots"][0]["memberships"][0]["customer_id"] == "customer-old"
    revision = params["p_candidate_snapshots"][0]["revisions"][0]
    assert revision["normalized_snapshot"]["product_type"] == "reliable laptop"
    assert "updated_at" not in revision["normalized_snapshot"]
    table, diagnostics, on_conflict = client.upserts[0]
    assert table == "demand_grouping_candidate_diagnostics"
    assert on_conflict == "demand_id,candidate_group_id"
    assert diagnostics[0]["assessment_status"] == "compatible"
    assert diagnostics[0]["candidate_demand_ids"] == ["existing-demand"]
    assert diagnostics[0]["cosine_distance"] == 0.1
    assert diagnostics[0]["reason_codes"] == []
    assert diagnostics[0]["evaluated_at"]
    assert len(embeddings.generate_calls) == 1
    assert embeddings.persist_calls[0][0] == "new-demand"
    assert embeddings.search_calls[0][1]["exclude_demand_id"] == "new-demand"
    assert all(
        name not in {"propose_demand_group_merge_v1", "merge_demand_group_request_v1"}
        for name, _ in client.rpc_calls
    )


@pytest.mark.anyio
async def test_incompatible_budget_candidate_creates_a_separate_group(embeddings):
    client, demand = _candidate_client(candidate_budget=(300, 400))

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    _, params = next(
        (name, params) for name, params in client.rpc_calls
        if name == "attach_demand_to_canonical_group_v1"
    )
    assert params["p_target_group_id"] is None
    assert params["p_singleton_reason"] == "no_compatible_existing_group"
    diagnostic = client.upserts[0][1][0]
    assert diagnostic["assessment_status"] == "rejected"
    assert "budget_intersection_empty" in diagnostic["reason_codes"]


@pytest.mark.anyio
async def test_review_assessment_without_compatible_candidates_creates_singleton(embeddings):
    client, demand = _candidate_client(unknown=True)

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] is None
    assert attach["p_singleton_reason"] == "no_compatible_existing_group"
    diagnostics = client.upserts[0][1]
    assert diagnostics[0]["assessment_status"] == "needs_review"
    assert "mandatory_value_unparsed:unrecognized_constraint" in diagnostics[0]["reason_codes"]


@pytest.mark.anyio
async def test_review_candidate_is_skipped_for_next_compatible_candidate(embeddings, monkeypatch):
    client, demand = _candidate_client()
    second_demand_id = _append_candidate_group(client, "group-2")
    embeddings.candidates.append(DemandEmbeddingCandidate(second_demand_id, 0.2, 0.8))
    monkeypatch.setattr(
        grouping_service,
        "assess_group_compatibility",
        lambda records: CompatibilityAssessment(
            "needs_review" if records[0]["group_id"] == "group-1" else "compatible",
            ("unparsed",) if records[0]["group_id"] == "group-1" else (),
            0.0,
        ),
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] == "group-2"
    assert [row["assessment_status"] for row in client.upserts[0][1]] == ["needs_review", "compatible"]


@pytest.mark.anyio
async def test_rejected_candidate_is_skipped_for_next_compatible_candidate(embeddings, monkeypatch):
    client, demand = _candidate_client()
    _append_candidate_group(client, "group-2")
    embeddings.candidates.append(DemandEmbeddingCandidate("group-2-demand", 0.2, 0.8))
    monkeypatch.setattr(
        grouping_service,
        "assess_group_compatibility",
        lambda records: CompatibilityAssessment(
            "rejected" if records[0]["group_id"] == "group-1" else "compatible",
            ("conflict",) if records[0]["group_id"] == "group-1" else (),
            0.0,
        ),
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] == "group-2"
    assert [row["assessment_status"] for row in client.upserts[0][1]] == ["rejected", "compatible"]


@pytest.mark.anyio
async def test_all_review_and_rejected_candidates_create_new_group(embeddings, monkeypatch):
    client, demand = _candidate_client()
    _append_candidate_group(client, "group-2")
    embeddings.candidates.append(DemandEmbeddingCandidate("group-2-demand", 0.2, 0.8))
    monkeypatch.setattr(
        grouping_service,
        "assess_group_compatibility",
        lambda records: CompatibilityAssessment(
            "needs_review" if records[0]["group_id"] == "group-1" else "rejected",
            ("unresolved",) if records[0]["group_id"] == "group-1" else ("conflict",),
            0.0,
        ),
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] is None
    assert attach["p_singleton_reason"] == "no_compatible_existing_group"
    assert attach["p_expected_candidate_group_ids"] == ["group-1", "group-2"]
    assert [snapshot["group_id"] for snapshot in attach["p_candidate_snapshots"]] == ["group-1", "group-2"]
    assert [row["assessment_status"] for row in client.upserts[0][1]] == ["needs_review", "rejected"]


@pytest.mark.anyio
async def test_mixed_candidate_outcomes_attach_to_best_compatible_group(embeddings, monkeypatch):
    client, demand = _candidate_client()
    _append_candidate_group(client, "group-2")
    _append_candidate_group(client, "group-3")
    _append_candidate_group(client, "group-4")
    embeddings.candidates.extend([
        DemandEmbeddingCandidate("group-2-demand", 0.15, 0.85),
        DemandEmbeddingCandidate("group-3-demand", 0.30, 0.70),
        DemandEmbeddingCandidate("group-4-demand", 0.25, 0.75),
    ])
    assessments = {
        "group-1": CompatibilityAssessment("rejected", ("conflict",), 0.0),
        "group-2": CompatibilityAssessment("needs_review", ("unparsed",), 0.0),
        "group-3": CompatibilityAssessment("compatible", (), 0.0),
        "group-4": CompatibilityAssessment("compatible", (), 0.0),
    }
    monkeypatch.setattr(
        grouping_service,
        "assess_group_compatibility",
        lambda records: assessments[records[0]["group_id"]],
    )

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "group-4"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] == "group-4"
    assert [row["assessment_status"] for row in client.upserts[0][1]] == [
        "rejected", "needs_review", "compatible", "compatible",
    ]


@pytest.mark.anyio
async def test_unrelated_ineligible_family_group_does_not_block_singleton(embeddings, monkeypatch):
    client, demand = _candidate_client()
    _append_candidate_group(client, "unrelated-group")
    client.rows["demand_groups"][1]["group_status"] = "Approved"
    monkeypatch.setattr(
        grouping_service,
        "assess_group_compatibility",
        lambda _records: CompatibilityAssessment("rejected", ("conflict",), 0.0),
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] is None
    assert attach["p_singleton_reason"] == "no_compatible_existing_group"
    assert client.upserts[0][1][1]["assessment_status"] == "ineligible"
    assert client.upserts[0][1][1]["candidate_demand_ids"] == []


@pytest.mark.anyio
async def test_hp_laptop_all_review_and_rejected_candidates_create_new_group(embeddings, monkeypatch):
    client, demand = _candidate_client()
    demand.update({"title": "HP laptop", "category": "Computers", "target_quantity": 1})
    new_normalized = client.rows["demand_normalized_requirements"][0]
    new_normalized.update({
        "product_type": "HP laptop",
        "normalized_product_name": "HP laptop",
        "normalized_category": "Computers",
        "commercial_constraints": {
            "minimum_target_unit_price": 55000,
            "maximum_target_unit_price": 60000,
            "currency": "INR",
            "unit": "units",
        },
    })
    _append_candidate_group(client, "group-2")
    embeddings.candidates.append(DemandEmbeddingCandidate("group-2-demand", 0.2, 0.8))
    monkeypatch.setattr(
        grouping_service,
        "assess_group_compatibility",
        lambda records: CompatibilityAssessment(
            "needs_review" if records[0]["group_id"] == "group-1" else "rejected",
            ("unresolved",) if records[0]["group_id"] == "group-1" else ("conflict",),
            0.0,
        ),
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    assert embeddings.events == ["read", "generate", "persist", "search"]
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] is None
    assert attach["p_singleton_reason"] == "no_compatible_existing_group"
    assert attach["p_expected_candidate_group_ids"] == ["group-1", "group-2"]
    assert [row["assessment_status"] for row in client.upserts[0][1]] == ["needs_review", "rejected"]


@pytest.mark.anyio
async def test_assessment_receives_new_demand_and_all_candidate_group_members(embeddings, monkeypatch):
    client, demand = _candidate_client()
    second_member = _demand(
        "second-member", "group-1", "customer-second", "business laptop", 1,
        "2026-01-02T12:00:00Z",
    )
    client.rows["demands"].append(second_member)
    client.rows["demand_group_members"].append({
        "group_id": "group-1",
        "customer_id": "customer-second",
        "demand_id": "second-member",
        "quantity_committed": 1,
        "membership_status": "active",
        "removed_at": None,
    })
    client.rows["demand_normalized_requirements"].append(_normalized("second-member", "business laptop"))
    assessed_ids = []
    assess = grouping_service.assess_group_compatibility

    def capture_records(records):
        assessed_ids.extend(row["id"] for row in records)
        return assess(records)

    monkeypatch.setattr(grouping_service, "assess_group_compatibility", capture_records)
    embeddings.candidates = [DemandEmbeddingCandidate("existing-demand", 0.1, 0.9)]

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "group-1"
    assert set(assessed_ids) == {"new-demand", "existing-demand", "second-member"}


@pytest.mark.anyio
async def test_unapproved_product_category_alias_is_embedded_but_never_attached(embeddings):
    client = _Client({})
    demand = _demand(
        "new-demand", None, "customer-new", "Bluetooth speaker", 1,
        "2026-01-01T00:00:00Z",
    )
    result = await try_group_new_demand(demand, client)

    assert result["group_id"] is None
    assert client.rpc_calls == []
    assert len(embeddings.generate_calls) == 1
    assert embeddings.persist_calls[0][0] == "new-demand"
    assert len(embeddings.search_calls) == 1
    assert embeddings.events == ["read", "generate", "persist", "search"]


@pytest.mark.anyio
async def test_reviewed_headphone_category_retries_through_strict_grouping(embeddings, monkeypatch):
    client, demand = _headphone_candidate_client()
    assessments = []
    assess = grouping_service.assess_group_compatibility

    def record_assessment(records):
        result = assess(records)
        assessments.append(result)
        return result

    monkeypatch.setattr(grouping_service, "assess_group_compatibility", record_assessment)

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    assert result["group_id"] == "group-1"
    assert len(embeddings.generate_calls) == 1
    assert embeddings.persist_calls[0][0] == "new-demand"
    assert embeddings.search_calls[0][1]["exclude_demand_id"] == "new-demand"
    assert [assessment.status for assessment in assessments] == ["compatible"]
    family_call = next(
        params for name, params in client.rpc_calls
        if name == "list_demand_group_family_candidates_v1"
    )
    assert family_call == {
        "p_product_family_key": "wireless_headphones",
        "p_category_key": "wireless_headphones",
    }
    attach_call = next(
        params for name, params in client.rpc_calls
        if name == "attach_demand_to_canonical_group_v1"
    )
    assert attach_call["p_target_group_id"] == "group-1"


@pytest.mark.anyio
async def test_valid_stored_embedding_is_reused_without_generation_or_write(embeddings, monkeypatch):
    from app.core.config import get_settings

    client, demand = _candidate_client()
    normalized_source = client.rows["demand_normalized_requirements"][0]
    embedding_input = {
        **demand,
        "product_name": demand["title"],
        "product_type": normalized_source["product_type"],
        "normalized_requirements": normalized_source,
    }
    settings = get_settings()
    stored = StoredDemandEmbedding(
        embedding=[0.5] * EMBEDDING_DIMENSIONS,
        embedding_model=settings.OLLAMA_EMBEDDING_MODEL,
        embedding_version=settings.OLLAMA_EMBEDDING_VERSION,
        input_hash=calculate_embedding_input_hash(
            build_embedding_input(embedding_input),
            settings.OLLAMA_EMBEDDING_VERSION,
        ),
    )
    monkeypatch.setattr(
        grouping_service,
        "get_demand_embedding",
        lambda demand_id, client=None: (embeddings.events.append("read"), stored)[1],
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    assert embeddings.events == ["read", "search"]
    assert embeddings.generate_calls == []
    assert embeddings.persist_calls == []
    assert embeddings.search_calls[0][0] == stored.embedding


@pytest.mark.anyio
async def test_older_headphone_requirements_remain_compatible_on_retry(embeddings):
    client, demand = _headphone_candidate_client()
    client.rows["demand_normalized_requirements"][0]["mandatory_requirements"] = (
        _older_headphone_requirements()
    )

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "group-1"
    assert client.upserts[0][1][0]["assessment_status"] == "compatible"


@pytest.mark.anyio
async def test_similar_headphone_title_in_different_category_stays_in_review(embeddings):
    client, demand = _candidate_client()
    demand.update({
        "title": "wireless over-ear headphones",
        "category": "Automotive > Audio Systems",
        "grouping_status": "needs_review",
        "grouping_review_reason": "unrecognized_product_category_alias",
    })

    result = await try_group_new_demand(demand, client)

    assert result == demand
    assert result["group_id"] is None
    assert result["grouping_status"] == "needs_review"
    assert result["grouping_review_reason"] == "unrecognized_product_category_alias"
    assert client.rpc_calls == []
    assert len(embeddings.generate_calls) == 1
    assert embeddings.persist_calls[0][0] == "new-demand"
    assert len(embeddings.search_calls) == 1
    assert embeddings.events == ["read", "generate", "persist", "search"]


@pytest.mark.anyio
async def test_unparsed_mandatory_requirement_is_embedded_before_review(embeddings, monkeypatch):
    client, demand = _candidate_client()
    client.rows["demand_groups"] = []
    client.rows["demand_group_members"] = []
    client.rows["demands"] = []
    client.rows["demand_normalized_requirements"][0]["mandatory_requirements"] = [
        {"name": "unrecognized_constraint", "value": "opaque"},
    ]
    demand["description"] = "For everyday work"
    demand["technical_specifications"] = [
        {"name": "memory", "value": "16 GB", "mandatory": True},
    ]
    embeddings.candidates = []
    record_review = grouping_service._record_review

    def record_review_after_embedding(client, demand_id, customer_id, reason):
        embeddings.events.append("review")
        return record_review(client, demand_id, customer_id, reason)

    monkeypatch.setattr(grouping_service, "_record_review", record_review_after_embedding)

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert client.rpc_calls[-1][1]["p_reason"] == "candidate_compatibility_unresolved"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)
    assert embeddings.events == ["read", "generate", "persist", "search", "review"]
    embedding_input = build_embedding_input(embeddings.generate_calls[0])
    assert "Product: laptop suitable" in embedding_input
    assert "Category: Computers" in embedding_input
    assert "Description: For everyday work" in embedding_input
    assert "- memory: 16 GB" in embedding_input
    assert "unrecognized_constraint" not in embedding_input


@pytest.mark.anyio
async def test_no_semantic_candidates_preserves_safe_singleton_creation(embeddings):
    client, demand = _candidate_client()
    client.rows["demand_groups"] = []
    client.rows["demand_group_members"] = []
    client.rows["demands"] = []
    embeddings.candidates = []

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    _, params = next(call for call in client.rpc_calls if call[0] == "attach_demand_to_canonical_group_v1")
    assert params["p_target_group_id"] is None
    assert params["p_singleton_reason"] == "no_compatible_existing_group"
    assert params["p_expected_candidate_group_ids"] == []
    assert params["p_candidate_snapshots"] == []


@pytest.mark.anyio
async def test_successful_empty_candidate_group_list_creates_singleton(embeddings):
    client, demand = _candidate_client()
    client.rows["demand_groups"] = []

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    attach = next(params for name, params in client.rpc_calls if name == "attach_demand_to_canonical_group_v1")
    assert attach["p_target_group_id"] is None
    assert attach["p_singleton_reason"] == "no_compatible_existing_group"
    assert attach["p_expected_candidate_group_ids"] == []
    assert attach["p_candidate_snapshots"] == []
    assert client.upserts == []


@pytest.mark.anyio
async def test_compatible_group_without_embeddings_is_still_assessed(embeddings):
    client, demand = _candidate_client()
    embeddings.candidates = []

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "group-1"
    _, params = next(call for call in client.rpc_calls if call[0] == "attach_demand_to_canonical_group_v1")
    assert params["p_target_group_id"] == "group-1"
    assert params["p_expected_candidate_group_ids"] == ["group-1"]


@pytest.mark.anyio
async def test_compatible_group_beyond_twenty_search_hits_is_still_assessed(embeddings):
    client, demand = _candidate_client(candidate_budget=(300, 400))
    search_hit_ids = [
        _append_candidate_group(client, f"group-{index}", budget=(300, 400))
        for index in range(2, 22)
    ]
    compatible_demand_id = _append_candidate_group(client, "group-22")
    embeddings.candidates = [
        DemandEmbeddingCandidate(demand_id, 0.01 * index, 0.99)
        for index, demand_id in enumerate(search_hit_ids, start=1)
    ]

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "group-22"
    _, params = next(call for call in client.rpc_calls if call[0] == "attach_demand_to_canonical_group_v1")
    assert params["p_target_group_id"] == "group-22"
    assert len(params["p_expected_candidate_group_ids"]) == 22
    assert compatible_demand_id not in {candidate.demand_id for candidate in embeddings.candidates}


@pytest.mark.anyio
async def test_compatible_group_beyond_cosine_threshold_is_still_assessed(embeddings):
    client, demand = _candidate_client(candidate_budget=(300, 400))
    _append_candidate_group(client, "compatible-group")
    embeddings.candidates = [DemandEmbeddingCandidate("existing-demand", 0.1, 0.9)]

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "compatible-group"
    _, params = next(call for call in client.rpc_calls if call[0] == "attach_demand_to_canonical_group_v1")
    assert params["p_target_group_id"] == "compatible-group"


@pytest.mark.anyio
async def test_only_compatible_non_search_hit_group_is_selected(embeddings):
    client, demand = _candidate_client(candidate_budget=(300, 400))
    compatible_demand_id = _append_candidate_group(client, "compatible-group")
    embeddings.candidates = [DemandEmbeddingCandidate("existing-demand", 0.05, 0.95)]

    result = await try_group_new_demand(demand, client)

    assert result["group_id"] == "compatible-group"
    assert compatible_demand_id not in {candidate.demand_id for candidate in embeddings.candidates}


@pytest.mark.anyio
async def test_family_candidate_list_failure_routes_to_review_without_singleton(embeddings):
    client, demand = _candidate_client()
    client.fail_family_candidate_read = True

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_candidate_member_read_failure_routes_to_review_without_singleton(embeddings):
    client, demand = _candidate_client()
    client.fail_read = lambda table, _filters: table == "demand_group_members"

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_candidate_requirement_read_failure_routes_to_review_without_singleton(embeddings):
    client, demand = _candidate_client()
    client.fail_read = lambda table, filters: (
        table == "demand_normalized_requirements"
        and isinstance(filters.get("demand_id"), list)
    )

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_compatibility_assessment_exception_routes_to_review_without_singleton(embeddings, monkeypatch):
    client, demand = _candidate_client()

    def fail_assessment(_records):
        raise RuntimeError("assessment unavailable")

    monkeypatch.setattr(grouping_service, "assess_group_compatibility", fail_assessment)

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)
    assert client.upserts[0][1][0]["reason_codes"] == ["candidate_compatibility_assessment_failed"]


@pytest.mark.anyio
async def test_diagnostics_persistence_failure_routes_to_review_without_singleton(embeddings):
    client, demand = _candidate_client()
    client.fail_diagnostics_write = True

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_embedding_generation_failure_routes_to_review(embeddings):
    client, demand = _candidate_client()
    embeddings.generate_error = RuntimeError("provider unavailable")

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1] == (
        "set_demand_grouping_review_v1",
        {
            "p_demand_id": "new-demand",
            "p_customer_id": "customer-new",
            "p_reason": "compatibility_assessment_failed",
        },
    )
    assert embeddings.persist_calls == []
    assert embeddings.search_calls == []
    assert embeddings.events == ["read", "generate"]


@pytest.mark.anyio
async def test_embedding_persistence_failure_routes_to_review(embeddings):
    client, demand = _candidate_client()
    embeddings.persist_error = RuntimeError("write failed")

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert client.rpc_calls[-1][1]["p_reason"] == "compatibility_assessment_failed"
    assert embeddings.search_calls == []
    assert embeddings.events == ["read", "generate", "persist"]
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_candidate_rpc_failure_routes_to_review(embeddings):
    client, demand = _candidate_client()
    embeddings.search_error = RuntimeError("rpc unavailable")

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_canonical_rpc_failure_does_not_report_a_grouped_demand(embeddings):
    client, demand = _candidate_client()
    client.fail_attach = True

    with pytest.raises(RuntimeError, match="canonical attachment unavailable"):
        await try_group_new_demand(demand, client)

    assert sum(name == "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls) == 1


@pytest.mark.anyio
async def test_ineligible_semantic_candidate_is_not_attached(embeddings):
    client, demand = _candidate_client()
    client.rows["demand_groups"][0]["status"] = "Approved"

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_semantic_candidate_with_incomplete_membership_fails_closed(embeddings):
    client, demand = _candidate_client()
    client.rows["demand_group_members"].clear()

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "needs_review"
    assert client.rpc_calls[-1][0] == "set_demand_grouping_review_v1"
    assert all(name != "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls)
    assert client.upserts[0][1][0]["assessment_status"] == "ineligible"
    assert "candidate_group_membership_incomplete" in client.upserts[0][1][0]["reason_codes"]


@pytest.mark.anyio
async def test_same_customer_demands_delegate_quantity_aggregation_to_atomic_rpc(embeddings):
    client, demand = _candidate_client()
    client.rows["demands"][0]["customer_id"] = "customer-new"
    client.rows["demand_group_members"][0]["customer_id"] = "customer-new"

    result = await try_group_new_demand(demand, client)

    assert result["grouping_status"] == "grouped"
    _, params = next(call for call in client.rpc_calls if call[0] == "attach_demand_to_canonical_group_v1")
    assert params["p_customer_id"] == "customer-new"
    assert params["p_target_group_id"] == "group-1"
    assert params["p_candidate_snapshots"][0]["memberships"][0]["quantity_committed"] == 2


@pytest.mark.anyio
async def test_duplicate_grouping_retry_does_not_repeat_embedding_or_attachment(embeddings):
    client, demand = _candidate_client()

    first = await try_group_new_demand(demand, client)
    second = await try_group_new_demand(first, client)

    assert second == first
    assert len(embeddings.generate_calls) == 1
    assert len(embeddings.persist_calls) == 1
    assert len(embeddings.search_calls) == 1
    assert sum(name == "attach_demand_to_canonical_group_v1" for name, _ in client.rpc_calls) == 1
    assert all(name != "create_customer_demand_v2" for name, _ in client.rpc_calls)


@pytest.mark.anyio
async def test_missing_embedding_is_generated_persisted_before_candidate_search(embeddings):
    client, demand = _candidate_client()

    await try_group_new_demand(demand, client)

    assert embeddings.events == ["read", "generate", "persist", "search"]
    assert embeddings.read_calls == ["new-demand"]