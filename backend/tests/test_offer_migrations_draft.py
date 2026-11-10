from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"


def migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8").lower()


def test_draft_migrations_have_a_contiguous_numeric_order():
    expected = (
        "020_merchant_offers_group_id_draft.sql",
        "021_semantic_group_reconciliation_draft.sql",
        "022_merchant_offer_writes_draft.sql",
        "023_offer_acceptance_draft.sql",
        "024_demand_embeddings_draft.sql",
        "025_wireless_headphone_category_alias_draft.sql",
        "026_grouping_retry_safety_draft.sql",
    )

    assert all((MIGRATIONS / name).is_file() for name in expected)
    assert [name[:3] for name in expected] == ["020", "021", "022", "023", "024", "025", "026"]
    assert not any(path.name.startswith("019b_") for path in MIGRATIONS.glob("*.sql"))


def test_legacy_offer_currency_is_preserved_when_it_cannot_be_inferred():
    sql = migration("022_merchant_offer_writes_draft.sql")

    assert "set currency = coalesce(" in sql
    assert "where offer.currency is null" in sql
    assert "where (currency is not null and currency !~ '^[a-z]{3}$')" in sql
    assert "check (currency is null or currency ~ '^[a-z]{3}$')" in sql
    assert "alter column currency set not null" not in sql
    assert "insert into public.merchant_offers (" in sql
    assert "offer_code, group_id, currency, merchant_id" in sql


def test_semantic_group_attachment_requires_and_propagates_one_currency():
    sql = migration("021_semantic_group_reconciliation_draft.sql")

    assert "'currency', demand_group.currency" in sql
    assert "demand_group.currency is null or demand_group.currency = v_demand.currency" in sql
    assert "demand.currency is distinct from v_demand.currency" in sql
    assert "currency = v_demand.currency" in sql


def test_offer_acceptance_checks_locked_currency_and_propagates_demand_terms():
    sql = migration("023_offer_acceptance_draft.sql")
    function_start = sql.index("create or replace function public.accept_merchant_offer_v1")
    body_start = sql.index("as $function$", function_start) + len("as $function$")
    body_end = sql.index("$function$;", body_start)
    body = sql[body_start:body_end]

    demand_lock = body.index("where id = p_demand_id and customer_id = p_customer_id for update")
    offer_lock = body.index("where id = p_offer_id for update")
    currency_check = body.index("if v_demand.currency is null")
    assert demand_lock < offer_lock < currency_check
    assert "v_offer.currency is distinct from v_demand.currency" in body
    assert "selected_quantity, selected_unit, agreed_unit_price, currency" in body
    assert "v_demand.target_quantity, v_demand.unit, v_offer.unit_price, v_demand.currency" in body
    assert "'quantity', v_demand.target_quantity, 'total_price', v_total" in body
    assert "insert into public.payments (order_id, amount, currency, status, provider)" in body
    assert "values (v_order.id, v_total, v_demand.currency" in body
    assert "'usd'" not in body
    assert "'units'" not in body


def test_fractional_quantities_use_numeric_end_to_end():
    offer_writes = migration("022_merchant_offer_writes_draft.sql")
    acceptance = migration("023_offer_acceptance_draft.sql")

    assert "alter column min_order_quantity type numeric" in offer_writes
    assert "add column if not exists quoted_quantity numeric" in offer_writes
    assert "v_moq numeric;" in offer_writes
    assert "p_expected_quantity numeric" in acceptance
    assert "alter column quantity type numeric using quantity::numeric" in acceptance
    assert acceptance.count("alter column quantity type numeric using quantity::numeric") == 2
    assert "v_demand.target_quantity < 1" not in acceptance
    assert "v_demand.target_quantity <= 0" in acceptance


def test_acceptance_preflights_duplicates_and_enforces_service_role_rpc_access():
    sql = migration("023_offer_acceptance_draft.sql")
    duplicate_preflight = sql.index("do $acceptance_preflight$")
    unique_index = sql.index("create unique index if not exists uq_customer_offer_selection_demand")

    assert duplicate_preflight < unique_index
    assert "duplicate customer/demand selections require manual review" in sql
    assert "duplicate orders per selection require manual review" in sql
    assert "auth.role() is distinct from 'service_role'" in sql
    assert "revoke all on function public.accept_merchant_offer_v1" in sql
    assert "to service_role" in sql
    assert "notify pgrst, 'reload schema'" in sql


def test_offer_write_rpc_signatures_and_privileges_are_service_role_only():
    sql = migration("022_merchant_offer_writes_draft.sql")

    assert "create or replace function public.submit_merchant_offer_v1(" in sql
    assert "p_merchant_id uuid, p_group_id uuid, p_offer_code text, p_terms jsonb" in sql
    assert "create or replace function public.revise_merchant_offer_v1(" in sql
    assert "p_merchant_id uuid, p_offer_id uuid, p_unit_price numeric, p_expected_unit_price numeric" in sql
    assert "revoke insert, update, delete, truncate on public.merchant_offers from public, anon, authenticated" in sql
    assert "alter table public.offer_status_history enable row level security" in sql
    assert "grant execute on function public.submit_merchant_offer_v1(uuid, uuid, text, jsonb) to service_role" in sql
    assert "grant execute on function public.revise_merchant_offer_v1(uuid, uuid, numeric, numeric) to service_role" in sql
    assert "notify pgrst, 'reload schema'" in sql


def test_offer_write_reruns_validate_indexes_and_replace_rpc_definitions():
    group_reference = migration("020_merchant_offers_group_id_draft.sql")
    offer_writes = migration("022_merchant_offer_writes_draft.sql")
    acceptance = migration("023_offer_acceptance_draft.sql")

    assert "create unique index if not exists uq_merchant_offers_group_merchant" in group_reference
    assert "group_merchant_index_contract" in group_reference
    assert "create or replace function public.lock_merchant_offer_group_v1" in offer_writes
    assert "create or replace function public.submit_merchant_offer_v1" in offer_writes
    assert "create or replace function public.revise_merchant_offer_v1" in offer_writes
    assert "create unique index if not exists uq_customer_offer_selection_demand" in acceptance
    assert "create unique index if not exists uq_orders_selection" in acceptance
    assert "acceptance_index_contract" in acceptance


def test_embedding_draft_is_one_shot_and_atomic():
    sql = migration("024_demand_embeddings_draft.sql")

    assert "this is a one-shot migration draft" in sql
    assert "existing demand embedding objects require explicit schema review" in sql
    assert "begin;" in sql
    assert "commit;" in sql
    assert "create extension" not in sql