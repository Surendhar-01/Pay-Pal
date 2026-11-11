from pathlib import Path

from app.services.demand_family_taxonomy import canonical_demand_family


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "migrations"
    / "021_semantic_group_reconciliation_draft.sql"
)
OFFER_SCHEMA_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "migrations"
    / "020_merchant_offers_group_id_draft.sql"
)
HEADPHONE_ALIAS_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "migrations"
    / "025_wireless_headphone_category_alias_draft.sql"
)
GROUPING_RETRY_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "migrations"
    / "026_grouping_retry_safety_draft.sql"
)
HP_LAPTOP_ALIAS_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "migrations"
    / "029_hp_laptop_category_alias_draft.sql"
)


def test_migration_is_an_explicit_draft_without_a_merge_invocation():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "draft only" in sql
    assert "public.merge_demand_group_request_v1(" in sql
    assert "select public.merge_demand_group_request_v1(" not in sql
    assert "update public.demands" in sql
    assert "update public.demand_group_members" in sql


def test_migration_requires_approval_and_uses_a_transactional_merge_rpc():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "request_status <> 'approved'" in sql
    assert "for update" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "normalized_revisions" in sql
    assert "'reason', 'stale_approved_proposal'" in sql
    assert "raise exception 'merge postconditions failed'" in sql
    assert "request_status = 'merged'" in sql


def test_candidate_function_is_defined_after_redirect_table():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert sql.index("create table if not exists public.demand_group_redirects") < sql.index(
        "create or replace function public.list_demand_group_family_candidates_v1"
    )


def test_headphone_category_alias_is_exact_and_reviewed_for_existing_installations():
    bootstrap_sql = MIGRATION.read_text(encoding="utf-8").lower()
    forward_sql = " ".join(HEADPHONE_ALIAS_MIGRATION.read_text(encoding="utf-8").lower().split())
    exact_alias = (
        "'wireless over ear headphones', 'electronics audio wireless headphones', "
        "'wireless_headphones', 'wireless_headphones'"
    )

    assert exact_alias in bootstrap_sql
    assert exact_alias in forward_sql
    assert "approval_reference" in forward_sql
    assert "approved, approved_at" in forward_sql
    assert "insert into public.demand_group_family_aliases" in forward_sql
    assert "on conflict (product_name_key, category_path_key) do nothing" in forward_sql


def test_hp_laptop_alias_migration_is_idempotent_and_matches_python_taxonomy():
    sql = " ".join(HP_LAPTOP_ALIAS_MIGRATION.read_text(encoding="utf-8").lower().split())
    alias = canonical_demand_family("Hp laptop", "Computers")

    assert alias is not None
    assert alias.product_family_key == "laptop"
    assert alias.category_key == "computer_laptops"
    assert "'hp laptop', 'computers', 'laptop', 'computer_laptops'" in sql
    assert "alias.approved is true" in sql
    assert "on conflict (product_name_key, category_path_key) do update" in sql
    assert "where alias.product_family_key = excluded.product_family_key" in sql
    assert "and alias.category_key = excluded.category_key" in sql
    assert "approved_at = coalesce(alias.approved_at, excluded.approved_at)" in sql
    assert "set approved = excluded.approved" in sql
    assert "notify pgrst, 'reload schema'" in sql
    assert sql.startswith("-- draft only:")
    assert sql.endswith("commit;")


def test_grouping_retry_migration_persists_diagnostics_and_guards_attachment():
    sql = GROUPING_RETRY_MIGRATION.read_text(encoding="utf-8").lower()

    assert "one-shot supabase migration" in sql
    assert "create table public.demand_grouping_candidate_diagnostics" in sql
    assert "assessment_status in ('compatible', 'rejected', 'needs_review', 'ineligible')" in sql
    assert "reason_codes jsonb" in sql
    assert "existing grouping diagnostics object requires explicit schema review" in sql
    assert "('orders', 'selection_id')" in sql
    assert "create trigger guard_demand_group_attachment_v1" in sql
    assert "old.group_id is null and new.group_id is not null" in sql
    assert "auth.role() is distinct from 'service_role'" in sql
    assert "tg_op = 'insert'" in sql
    assert "before insert or update of group_id on public.demands" in sql
    assert "member.demand_id = old.id" in sql
    assert "offer.demand_id = old.id" in sql
    assert "selection.demand_id = old.id" in sql
    assert "order_row.demand_id = old.id" in sql
    assert "does not rewrite existing demand, group, or membership data" in sql
    assert "revoke all on table public.demand_grouping_candidate_diagnostics from public, anon, authenticated, service_role" in " ".join(sql.split())
    assert "grant select, insert, update on table public.demand_grouping_candidate_diagnostics to service_role" in " ".join(sql.split())
    assert "grouping_diagnostics_acl_contract" in sql
    assert "aclexplode" in sql and "privilege.grantee not in (relation.relowner, v_service_role_oid)" in sql
    assert "grouping_guard_acl_contract" in sql


def test_grouping_retry_migration_fails_closed_on_function_and_trigger_collisions():
    sql = GROUPING_RETRY_MIGRATION.read_text(encoding="utf-8").lower()
    preflight_end = sql.index("$grouping_retry_preflight$;", sql.index("do $grouping_retry_preflight$"))
    preflight = sql[:preflight_end]

    assert "to_regprocedure('public.guard_demand_group_attachment_v1()') is not null" in preflight
    assert "from pg_trigger as trigger_info" in preflight
    assert "trigger_info.tgname = 'guard_demand_group_attachment_v1'" in preflight
    assert preflight_end < sql.index("create function public.guard_demand_group_attachment_v1()")
    assert preflight_end < sql.index("create trigger guard_demand_group_attachment_v1")
    assert "create or replace function public.guard_demand_group_attachment_v1()" not in sql
    assert "drop trigger if exists guard_demand_group_attachment_v1" not in sql


def test_grouping_retry_trigger_rejects_grouped_inserts_but_accepts_ungrouped_ones():
    sql = GROUPING_RETRY_MIGRATION.read_text(encoding="utf-8").lower()
    function_start = sql.index("create function public.guard_demand_group_attachment_v1()")
    body_start = sql.index("as $$", function_start) + len("as $$")
    body_end = sql.index("$$;", body_start)
    body = sql[body_start:body_end]
    insert_branch = body.split("if tg_op = 'insert' then", 1)[1].split(
        "if new.group_id is distinct from old.group_id", 1
    )[0]

    assert "if new.group_id is not null then" in insert_branch
    assert "grouped demands must be created ungrouped" in insert_branch
    assert insert_branch.index("raise exception") < insert_branch.index("return new")
    assert "new.status::text not in ('submitted', 'under review')" in body
    assert "new.grouping_status is distinct from 'grouped'" in body


def test_grouping_retry_diagnostics_acl_is_exact_and_rls_remains_enabled():
    sql = " ".join(GROUPING_RETRY_MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "alter table public.demand_grouping_candidate_diagnostics enable row level security" in sql
    assert "where relation.oid = v_table_oid and relation.relrowsecurity" in sql
    assert "from pg_policy as policy where policy.polrelid = v_table_oid" in sql
    assert "privilege.grantor <> relation.relowner" in sql
    assert "privilege.grantee not in (relation.relowner, v_service_role_oid)" in sql
    assert "privilege.privilege_type not in ('select', 'insert', 'update')" in sql
    assert "privilege.is_grantable" in sql
    assert "and not privilege.is_grantable) <> 3" in sql


def test_offer_group_schema_draft_is_nullable_and_references_groups_safely():
    sql = OFFER_SCHEMA_MIGRATION.read_text(encoding="utf-8").lower()

    assert "draft only" in sql
    assert "add column if not exists group_id uuid" in sql
    assert "merchant_offers.group_id must be nullable uuid" in sql
    assert "references public.demand_groups(id)" in sql
    assert "on delete restrict" in sql
    assert "offer.demand_id = demand.id" in sql
    assert "demand.group_id is not null" in sql
    assert "idx_merchant_offers_group_merchant" in sql
    assert "member.group_id = merchant_offers.group_id" in sql
    assert "member.membership_status = 'active'" in sql


def test_offer_group_write_policies_validate_merchant_and_canonical_group():
    sql = OFFER_SCHEMA_MIGRATION.read_text(encoding="utf-8").lower()
    insert_start = sql.index("create policy offers_insert")
    update_start = sql.index("create policy offers_update")
    insert_policy = sql[insert_start:update_start]
    update_policy = sql[update_start:sql.index("notify pgrst", update_start)]

    for policy in (insert_policy, update_policy):
        assert "merchant_id = auth.uid()" in policy
        assert "merchant.role::text = 'merchant'" in policy
        assert "group_id is not null" in policy
        assert "demand_id is not null" in policy
        assert "demand.group_id = merchant_offers.group_id" in policy
        assert "merchant_profile.status = 'approved'" in policy
        assert "demand_group.group_status = 'approved'" in policy
        assert "demand_group.bidding_status = 'open'" in policy

    assert "with check" in insert_policy
    assert "using" in update_policy
    assert "with check" in update_policy


def test_offer_group_unique_index_is_rerunnable_and_definition_checked():
    sql = OFFER_SCHEMA_MIGRATION.read_text(encoding="utf-8").lower()
    index_start = sql.index("create unique index if not exists uq_merchant_offers_group_merchant")
    contract_start = sql.index("do $group_merchant_index_contract$", index_start)
    contract_end = sql.index("$group_merchant_index_contract$;", contract_start)
    contract = sql[contract_start:contract_end]

    assert "on public.merchant_offers(group_id, merchant_id)" in sql[index_start:contract_start]
    assert "where group_id is not null and merchant_id is not null" in sql[index_start:contract_start]
    assert "from pg_index as index_info" in contract
    assert "index_info.indisunique" in contract
    assert "index_info.indisvalid" in contract
    assert "index_info.indisready" in contract
    assert "v_predicate is distinct from 'group_idisnotnullandmerchant_idisnotnull'" in contract
    assert "pg_get_indexdef(index_info.indexrelid, 1, true)" in contract
    assert "pg_get_indexdef(index_info.indexrelid, 2, true)" in contract
    assert "pg_get_expr(index_info.indpred, index_info.indrelid)" in contract
    assert "existing uq_merchant_offers_group_merchant index has an unexpected definition" in contract


def test_merge_request_rows_are_rpc_managed_and_plans_are_immutable():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "on public.demand_group_merge_requests for all" not in sql
    assert "revoke insert, update, delete, truncate" in sql
    assert "merge plan, proposal snapshot, and compatibility report are immutable" in sql
    assert "invalid merge-request state transition" in sql
    assert "before insert or update or delete on public.demand_group_merge_requests" in sql


def test_merge_lifecycle_rpcs_lock_family_groups_children_then_request():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    lock_sequence = [
        "pg_advisory_xact_lock(",
        "perform demand_group.id",
        "perform demand.id",
        "perform member.group_id",
        "perform normalized.demand_id",
    ]

    for function_name in (
        "propose_demand_group_merge_v1",
        "approve_demand_group_merge_v1",
        "merge_demand_group_request_v1",
    ):
        function_start = sql.index(f"create or replace function public.{function_name}")
        body_start = sql.index("as $$", function_start) + len("as $$")
        body_end = sql.index("$$;", body_start)
        body = sql[body_start:body_end]
        positions = [body.index(lock) for lock in lock_sequence]
        request_lock = body.rindex("for update")

        assert positions == sorted(positions)
        assert positions[-1] < request_lock


def test_offer_group_contract_is_explicit_and_unmodeled_order_reference_fails_closed():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "('merchant_offers', 'group_id')" in sql
    assert "'customer_offer_selections', 'merchant_offers'" in sql
    assert "offer.group_id in (v_request.survivor_group_id, v_request.source_group_id)" in sql
    assert "where offer.group_id = v_group_id" in sql
    assert "order_row.group_id" not in sql
    assert "offer.demand_id = any(v_demand_ids)" in sql
    assert "order_row.demand_id = any(v_demand_ids)" in sql


def test_merge_revision_snapshots_use_uuid_exact_coverage_and_idempotency_payload():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "count(distinct (revision.value->>'demand_id')::uuid)" in sql
    assert "where (revision.value->>'demand_id')::uuid = expected.demand_id" in sql
    assert "v_request.compatibility_report is distinct from p_compatibility_report" in sql
    assert "v_request.product_family_key is distinct from p_product_family_key" in sql


def test_candidate_snapshots_require_exact_group_demand_revision_and_member_coverage():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    function_start = sql.index(
        "create or replace function public.attach_demand_to_canonical_group_v1"
    )
    body_start = sql.index("as $$", function_start) + len("as $$")
    body_end = sql.index("$$;", body_start)
    body = sql[body_start:body_end]

    assert "count(distinct (snapshot.value->>'group_id')::uuid)" in body
    assert "candidate revision snapshot is incomplete" in body
    assert "to_jsonb(normalized) - array['created_at', 'updated_at', 'reviewed_at']" in body
    assert "v_current_normalized is distinct from v_revision->'normalized_snapshot'" in body
    assert "candidate membership snapshot must cover each member exactly once" in body
    assert body.index("perform member.group_id") < body.index("perform normalized.id")


def test_attachment_validates_existing_membership_before_update_and_returns_jsonb():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    function_start = sql.index(
        "create or replace function public.attach_demand_to_canonical_group_v1"
    )
    body_start = sql.index("as $$", function_start) + len("as $$")
    body_end = sql.index("$$;", body_start)
    body = sql[body_start:body_end]

    assert "v_saved jsonb;" in body
    assert body.index("existing group membership is inconsistent") < body.index(
        "update public.demands"
    )
    assert "original_target_price, original_budget" in body
    assert "select to_jsonb(demand) into v_saved" in body
    assert "return v_saved;" in body


def test_attachment_aggregates_multiple_demands_for_the_same_customer():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    function_start = sql.index(
        "create or replace function public.attach_demand_to_canonical_group_v1"
    )
    body_start = sql.index("as $$", function_start) + len("as $$")
    body_end = sql.index("$$;", body_start)
    body = sql[body_start:body_end]

    assert "sum(demand.target_quantity)" in body
    assert "group by demand.customer_id" in body
    assert "on conflict (group_id, customer_id) do update" in body
    assert "quantity_committed = excluded.quantity_committed" in body


def test_merge_has_no_pilot_specific_group_quantity_restrictions():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "approved current pair total must be" not in sql
    assert "d1a44ed5-f108-40a1-94bc-5158953c056f" not in sql


def test_redirect_rows_are_rpc_managed_and_membership_metadata_is_not_synthesized():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    redirect_definition = sql.index("create table if not exists public.demand_group_redirects")
    redirect_revoke = sql.index("on table public.demand_group_redirects")
    merge_definition = sql.index("create or replace function public.merge_demand_group_request_v1")

    assert redirect_definition < redirect_revoke < merge_definition
    assert "from public, anon, authenticated, service_role" in sql[redirect_revoke : redirect_revoke + 120]
    assert "conflicting original membership metadata blocks merge" in sql
    assert "count(distinct member.original_target_price) > 1" in sql
    assert "count(distinct member.original_budget) > 1" in sql
    assert "coalesce(" in sql
    assert "member.group_id = v_request.survivor_group_id" in sql


def test_approval_revalidates_snapshots_and_commits_stale_rejection():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    approval_start = sql.index(
        "create or replace function public.approve_demand_group_merge_v1"
    )
    body_start = sql.index("as $$", approval_start) + len("as $$")
    body_end = sql.index("$$;", body_start)
    approval_body = sql[body_start:body_end]
    trigger_start = sql.index(
        "create or replace function public.guard_demand_group_merge_request_v1"
    )
    trigger_body_start = sql.index("as $$", trigger_start) + len("as $$")
    trigger_body_end = sql.index("$$;", trigger_body_start)
    trigger_body = sql[trigger_body_start:trigger_body_end]
    stale_update = approval_body.index(
        "set request_status = 'rejected'"
    )
    approved_update = approval_body.index("set request_status = 'approved'", stale_update)

    assert "check (request_status in ('proposed', 'approved', 'rejected', 'merged'))" in sql
    assert "normalized_revision_changed" in approval_body
    assert "demand_set_or_quantity_changed" in approval_body
    assert "family_snapshot_changed" in approval_body
    assert "nullif(v_revision->>'normalized_updated_at', '')::timestamptz" in approval_body
    assert "'reason', 'stale_proposal'" in approval_body
    assert approval_body.index("return to_jsonb(v_request)", stale_update) < approved_update
    assert "raise exception" not in approval_body[stale_update:approved_update]
    assert "old.request_status = 'proposed' and new.request_status = 'rejected'" in trigger_body
    assert "new.merge_result->>'reason' = 'stale_proposal'" in trigger_body
    approval_snapshot_check = approval_body.index("v_request.proposal_snapshot is distinct from")
    approval_snapshot_end = approval_body.index("select * into v_survivor", approval_snapshot_check)
    assert "'reason', 'stale_proposal'" in approval_body[approval_snapshot_check:approval_snapshot_end]
    assert "'reason', 'stale_approved_proposal'" not in approval_body[approval_snapshot_check:approval_snapshot_end]
    assert "where request_status in ('proposed', 'approved', 'merged')" in sql


def test_approval_keeps_family_to_request_lock_order():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    function_start = sql.index(
        "create or replace function public.approve_demand_group_merge_v1"
    )
    body_start = sql.index("as $$", function_start) + len("as $$")
    body_end = sql.index("$$;", body_start)
    body = sql[body_start:body_end]
    lock_sequence = [
        "pg_advisory_xact_lock(",
        "perform demand_group.id",
        "perform demand.id",
        "perform member.group_id",
        "perform normalized.demand_id",
    ]
    positions = [body.index(lock) for lock in lock_sequence]

    assert positions == sorted(positions)
    assert positions[-1] < body.rindex("for update")


def test_proposal_persists_and_revalidates_exact_merge_snapshot():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    proposal_start = sql.index("create or replace function public.propose_demand_group_merge_v1")
    proposal_end = sql.index("$$;", proposal_start)
    proposal_definition = sql[proposal_start:proposal_end]
    proposal_body = proposal_definition[proposal_definition.index("as $$") + len("as $$"):]

    assert "proposal_snapshot jsonb not null" in sql
    assert "drop function if exists public.propose_demand_group_merge_v1(uuid, uuid, numeric, uuid[], text, text, text, uuid, jsonb)" in sql
    assert "p_proposal_snapshot jsonb" in proposal_definition
    assert "current_demand_group_merge_snapshot_v1(p_survivor_group_id, p_source_group_id)" in proposal_body
    assert "v_request.proposal_snapshot is distinct from p_proposal_snapshot" in proposal_body
    assert "'normalized_snapshot'" in proposal_body
    assert "to_jsonb(normalized)" in proposal_body
    assert "grant execute on function public.propose_demand_group_merge_v1(uuid, uuid, numeric, uuid[], text, text, text, uuid, jsonb, jsonb) to service_role" in sql
    assert "revoke all on function public.current_demand_group_merge_snapshot_v1(uuid, uuid)" in sql
    assert "grant execute on function public.propose_demand_group_merge_v1(uuid, uuid, numeric, uuid[], text, text, text, uuid, jsonb) to service_role" not in sql


def test_approved_stale_merge_is_terminal_and_releases_source_reservation():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    trigger_start = sql.index("create or replace function public.guard_demand_group_merge_request_v1")
    trigger_body = sql[sql.index("as $$", trigger_start) + len("as $$"):sql.index("$$;", trigger_start)]
    merge_start = sql.index("create or replace function public.merge_demand_group_request_v1")
    merge_body = sql[sql.index("as $$", merge_start) + len("as $$"):sql.index("$$;", merge_start)]
    snapshot_check = merge_body.index("v_request.proposal_snapshot is distinct from")
    snapshot_rejection = merge_body.index("set request_status = 'rejected'", snapshot_check)
    first_group_read = merge_body.index("select * into v_survivor", snapshot_check)

    assert "where request_status in ('proposed', 'approved', 'merged')" in sql
    assert "old.request_status = 'approved' and new.request_status = 'rejected'" in trigger_body
    assert "new.merge_result->>'reason' = 'stale_approved_proposal'" in trigger_body
    assert "new.merge_result->>'detail' in ('proposal_snapshot_changed', 'normalized_revision_changed')" in trigger_body
    assert "set request_status = 'rejected'" in merge_body
    assert snapshot_rejection < first_group_read
    assert "'reason', 'stale_approved_proposal'" in merge_body
    assert "raise exception 'normalized requirements changed after merge approval'" not in merge_body
    assert "exception when others" not in merge_body


def test_attachment_and_merge_serialize_before_their_different_row_lock_orders():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    merge_start = sql.index("create or replace function public.merge_demand_group_request_v1")
    merge_body = sql[sql.index("as $$", merge_start) + len("as $$"):sql.index("$$;", merge_start)]
    attach_start = sql.index("create or replace function public.attach_demand_to_canonical_group_v1")
    attach_body = sql[sql.index("as $$", attach_start) + len("as $$"):sql.index("$$;", attach_start)]

    advisory_key = "hashtextextended(p_category_key || ':' || p_product_family_key, 0)"
    assert advisory_key in attach_body
    assert "hashtextextended(v_request.category_key || ':' || v_request.product_family_key, 0)" in merge_body
    assert attach_body.index("pg_advisory_xact_lock(") < attach_body.index("for update")
    assert attach_body.index("for update") < attach_body.index("perform demand_group.id")
    assert merge_body.index("pg_advisory_xact_lock(") < merge_body.index("perform demand_group.id")
    assert merge_body.index("perform demand_group.id") < merge_body.index("perform demand.id")