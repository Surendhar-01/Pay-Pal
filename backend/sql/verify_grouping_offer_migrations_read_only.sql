-- READ-ONLY preflight for MarketTwin migrations 020-026.
-- Run against the intended project using a role that can inspect public catalogs
-- and the relevant rows. This file is not a migration and does not apply anything.
--
-- Status rows describe observed schema artifacts. The migration-history check is
-- reported separately because manual SQL application may not create history rows.
-- NOTICE messages contain conditional history, alias, and data-preflight results.

BEGIN TRANSACTION READ ONLY;

-- Discover the conventional Supabase migration-history relation without assuming
-- that it exists. The repository verifier references this name but documents its
-- existence as an assumption.
SELECT
    'supabase_migrations.schema_migrations' AS candidate_history_relation,
    to_regclass('supabase_migrations.schema_migrations') IS NOT NULL AS relation_exists,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'supabase_migrations'
          AND table_name = 'schema_migrations'
          AND column_name = 'version'
    ) AS has_version_column,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'supabase_migrations'
          AND table_name = 'schema_migrations'
          AND column_name = 'name'
    ) AS has_name_column;

-- Read migration history only if the relation and required columns exist.
DO $migration_history_read_only$
DECLARE
    history_relation REGCLASS := to_regclass('supabase_migrations.schema_migrations');
    has_version BOOLEAN;
    has_name BOOLEAN;
    migration_row RECORD;
    history_count BIGINT;
    name_matches BOOLEAN;
    history_names TEXT;
    recorded_022 BIGINT;
    recorded_023 BIGINT;
    recorded_024 BIGINT;
    recorded_025 BIGINT;
BEGIN
    IF history_relation IS NULL THEN
        RAISE NOTICE 'MIGRATION HISTORY: UNKNOWN; supabase_migrations.schema_migrations does not exist.';
        RETURN;
    END IF;

    SELECT
        COALESCE(bool_or(column_name = 'version'), FALSE),
        COALESCE(bool_or(column_name = 'name'), FALSE)
    INTO has_version, has_name
    FROM information_schema.columns
    WHERE table_schema = 'supabase_migrations'
      AND table_name = 'schema_migrations';

    IF NOT has_version THEN
        RAISE NOTICE 'MIGRATION HISTORY: UNKNOWN; history relation has no version column.';
        RETURN;
    END IF;

    FOR migration_row IN
        SELECT *
        FROM (VALUES
            ('020', 'merchant_offers_group_id_draft'),
            ('021', 'semantic_group_reconciliation_draft'),
            ('022', 'merchant_offer_writes_draft'),
            ('023', 'offer_acceptance_draft'),
            ('024', 'demand_embeddings_draft'),
            ('025', 'wireless_headphone_category_alias_draft'),
            ('026', 'grouping_retry_safety_draft')
        ) AS expected(version, migration_stem)
        ORDER BY version
    LOOP
        IF has_name THEN
            EXECUTE format(
                'SELECT count(*), '
                || 'COALESCE(bool_or('
                || 'lower(COALESCE(%1$I::text, '''')) = $2 '
                || 'OR lower(COALESCE(%1$I::text, '''')) = $3 '
                || 'OR regexp_replace(lower(COALESCE(%1$I::text, '''')), ''^[0-9]+[_-]'', '''') = $2'
                || '), FALSE), '
                || 'COALESCE(string_agg(COALESCE(%1$I::text, ''<NULL>''), '', '' ORDER BY %1$I::text), ''<no rows>'') '
                || 'FROM %2$s WHERE %3$I::text = $1',
                'name', history_relation, 'version'
            )
            INTO history_count, name_matches, history_names
            USING migration_row.version,
                  migration_row.migration_stem,
                  migration_row.version || '_' || migration_row.migration_stem;

            RAISE NOTICE 'MIGRATION HISTORY %: rows=%, expected version/name match=%, names=%',
                migration_row.version, history_count, name_matches, history_names;
        ELSE
            EXECUTE format(
                'SELECT count(*) FROM %s WHERE %I::text = $1',
                history_relation, 'version'
            )
            INTO history_count
            USING migration_row.version;

            RAISE NOTICE 'MIGRATION HISTORY %: rows=%; name column unavailable, so canonical migration name is UNKNOWN.',
                migration_row.version, history_count;
        END IF;
    END LOOP;

    EXECUTE format(
        'SELECT count(*) FILTER (WHERE %1$I::text = ''022''), '
        || 'count(*) FILTER (WHERE %1$I::text = ''023''), '
        || 'count(*) FILTER (WHERE %1$I::text = ''024''), '
        || 'count(*) FILTER (WHERE %1$I::text = ''025'') FROM %2$s',
        'version', history_relation
    ) INTO recorded_022, recorded_023, recorded_024, recorded_025;

    IF recorded_025 > 0 AND (recorded_022 = 0 OR recorded_023 = 0 OR recorded_024 = 0) THEN
        RAISE NOTICE 'OUT-OF-ORDER HISTORY WARNING: 025 is recorded while one or more of 022-024 are absent from migration history.';
    END IF;
END;
$migration_history_read_only$;

-- Required columns and their observed PostgreSQL types. This includes the
-- explicit preflights in 021-023 and the tables/columns used by their RPC bodies.
WITH required_columns(table_name, column_name, required_type) AS (
    VALUES
        ('demands', 'id', 'uuid'),
        ('demands', 'customer_id', 'uuid'),
        ('demands', 'group_id', 'uuid'),
        ('demands', 'status', 'application-defined'),
        ('demands', 'grouping_status', 'text'),
        ('demands', 'grouping_review_reason', 'text'),
        ('demands', 'target_quantity', 'numeric-compatible'),
        ('demands', 'currency', 'text'),
        ('demands', 'unit', 'text'),
        ('demands', 'demand_code', 'text'),
        ('demands', 'title', 'text'),
        ('demands', 'updated_at', 'timestamp-compatible'),
        ('demand_groups', 'id', 'uuid'),
        ('demand_groups', 'product_title', 'text'),
        ('demand_groups', 'category', 'text'),
        ('demand_groups', 'group_status', 'application-defined'),
        ('demand_groups', 'status', 'application-defined'),
        ('demand_groups', 'bidding_status', 'text'),
        ('demand_groups', 'bidding_start', 'timestamp-compatible'),
        ('demand_groups', 'bidding_deadline', 'timestamp-compatible'),
        ('demand_groups', 'currency', 'text'),
        ('demand_groups', 'total_quantity', 'numeric-compatible'),
        ('demand_groups', 'total_requested_quantity', 'numeric-compatible'),
        ('demand_groups', 'participants_count', 'integer-compatible'),
        ('demand_groups', 'participating_demand_count', 'integer-compatible'),
        ('demand_group_members', 'demand_id', 'uuid'),
        ('demand_group_members', 'customer_id', 'uuid'),
        ('demand_group_members', 'group_id', 'uuid'),
        ('demand_group_members', 'membership_status', 'text'),
        ('demand_group_members', 'removed_at', 'timestamp-compatible'),
        ('demand_group_members', 'joined_at', 'timestamp-compatible'),
        ('demand_group_members', 'quantity_committed', 'numeric-compatible'),
        ('demand_normalized_requirements', 'updated_at', 'timestamp-compatible'),
        ('demand_normalized_requirements', 'extraction_version', 'text'),
        ('demand_group_family_aliases', 'product_name_key', 'text'),
        ('demand_group_family_aliases', 'category_path_key', 'text'),
        ('demand_group_family_aliases', 'product_family_key', 'text'),
        ('demand_group_family_aliases', 'category_key', 'text'),
        ('demand_group_family_aliases', 'approval_reference', 'text'),
        ('demand_group_family_aliases', 'approved', 'boolean'),
        ('demand_group_family_aliases', 'approved_by', 'uuid'),
        ('demand_group_family_aliases', 'approved_at', 'timestamp-compatible'),
        ('demand_group_merge_requests', 'id', 'uuid'),
        ('demand_group_merge_requests', 'idempotency_key', 'text'),
        ('demand_group_merge_requests', 'survivor_group_id', 'uuid'),
        ('demand_group_merge_requests', 'source_group_id', 'uuid'),
        ('demand_group_merge_requests', 'expected_total_quantity', 'numeric-compatible'),
        ('demand_group_merge_requests', 'expected_demand_ids', 'uuid[]'),
        ('demand_group_merge_requests', 'product_family_key', 'text'),
        ('demand_group_merge_requests', 'category_key', 'text'),
        ('demand_group_merge_requests', 'compatibility_status', 'text'),
        ('demand_group_merge_requests', 'compatibility_report', 'jsonb'),
        ('demand_group_merge_requests', 'proposal_snapshot', 'jsonb'),
        ('demand_group_merge_requests', 'request_status', 'text'),
        ('demand_group_merge_requests', 'requested_by', 'uuid'),
        ('demand_group_merge_requests', 'requested_at', 'timestamp-compatible'),
        ('demand_group_merge_requests', 'approved_by', 'uuid'),
        ('demand_group_merge_requests', 'approved_at', 'timestamp-compatible'),
        ('demand_group_merge_requests', 'merged_at', 'timestamp-compatible'),
        ('demand_group_merge_requests', 'merge_result', 'jsonb'),
        ('demand_group_redirects', 'source_group_id', 'uuid'),
        ('demand_group_redirects', 'target_group_id', 'uuid'),
        ('demand_group_redirects', 'merge_request_id', 'uuid'),
        ('demand_group_redirects', 'merged_by', 'uuid'),
        ('demand_group_redirects', 'merged_at', 'timestamp-compatible'),
        ('merchant_offers', 'id', 'uuid'),
        ('merchant_offers', 'demand_id', 'uuid'),
        ('merchant_offers', 'group_id', 'uuid'),
        ('merchant_offers', 'merchant_id', 'uuid'),
        ('merchant_offers', 'min_order_quantity', 'numeric'),
        ('merchant_offers', 'quoted_quantity', 'numeric'),
        ('merchant_offers', 'currency', 'text'),
        ('merchant_offers', 'unit_price', 'numeric-compatible'),
        ('merchant_offers', 'total_offer_amount', 'numeric-compatible'),
        ('merchant_offers', 'status', 'text'),
        ('merchant_offers', 'offer_code', 'text'),
        ('offer_status_history', 'offer_id', 'uuid'),
        ('offer_status_history', 'previous_status', 'text'),
        ('offer_status_history', 'new_status', 'text'),
        ('offer_status_history', 'changed_by', 'uuid'),
        ('offer_status_history', 'remarks', 'text'),
        ('customer_offer_eligibility', 'demand_id', 'uuid'),
        ('customer_offer_selections', 'id', 'uuid'),
        ('customer_offer_selections', 'customer_id', 'uuid'),
        ('customer_offer_selections', 'demand_id', 'uuid'),
        ('customer_offer_selections', 'group_id', 'uuid'),
        ('customer_offer_selections', 'offer_id', 'uuid'),
        ('customer_offer_selections', 'merchant_id', 'uuid'),
        ('customer_offer_selections', 'selected_quantity', 'numeric-compatible'),
        ('customer_offer_selections', 'selected_unit', 'text'),
        ('customer_offer_selections', 'agreed_unit_price', 'numeric-compatible'),
        ('customer_offer_selections', 'currency', 'text'),
        ('customer_offer_selections', 'estimated_total', 'numeric-compatible'),
        ('customer_offer_selections', 'delivery_terms_snapshot', 'jsonb'),
        ('customer_offer_selections', 'product_terms_snapshot', 'jsonb'),
        ('customer_offer_selections', 'compliance_snapshot', 'jsonb'),
        ('orders', 'id', 'uuid'),
        ('orders', 'demand_id', 'uuid'),
        ('orders', 'customer_id', 'uuid'),
        ('orders', 'accepted_offer_id', 'uuid'),
        ('orders', 'selection_id', 'uuid'),
        ('orders', 'quantity', 'numeric after 023'),
        ('orders', 'accepted_terms', 'jsonb'),
        ('order_items', 'order_id', 'uuid'),
        ('order_items', 'demand_id', 'uuid'),
        ('order_items', 'quantity', 'numeric after 023'),
        ('order_items', 'unit_price', 'numeric-compatible'),
        ('order_items', 'total_price', 'numeric-compatible'),
        ('payments', 'order_id', 'uuid'),
        ('payments', 'amount', 'numeric-compatible'),
        ('payments', 'currency', 'text'),
        ('payments', 'status', 'text'),
        ('payments', 'provider', 'text'),
        ('profiles', 'id', 'uuid'),
        ('profiles', 'role', 'application-defined'),
        ('profiles', 'company', 'text'),
        ('profiles', 'full_name', 'text'),
        ('merchant_profiles', 'id', 'uuid'),
        ('merchant_profiles', 'status', 'text'),
        ('merchant_profiles', 'rating', 'numeric-compatible'),
        ('notifications', 'user_id', 'uuid'),
        ('notifications', 'title', 'text'),
        ('notifications', 'description', 'text'),
        ('notifications', 'type', 'text'),
        ('notifications', 'action_url', 'text')
)
SELECT
    required.table_name,
    required.column_name,
    required.required_type,
    actual.udt_name AS actual_udt_name,
    actual.data_type AS actual_data_type,
    actual.is_nullable,
    CASE WHEN actual.column_name IS NULL THEN 'MISSING' ELSE 'PRESENT; REVIEW TYPE' END AS result
FROM required_columns AS required
LEFT JOIN information_schema.columns AS actual
  ON actual.table_schema = 'public'
 AND actual.table_name = required.table_name
 AND actual.column_name = required.column_name
ORDER BY required.table_name, required.column_name;

-- Constraint and index definitions used by 020-023. Named objects with a
-- different definition are conflicts, not successful preflight results.
SELECT
    constraint_info.conrelid::regclass AS table_name,
    constraint_info.conname,
    constraint_info.contype,
    pg_get_constraintdef(constraint_info.oid, TRUE) AS definition,
    constraint_info.convalidated
FROM pg_constraint AS constraint_info
WHERE constraint_info.conname IN (
    'merchant_offers_group_id_fkey',
    'demands_grouping_status_check',
    'merchant_offers_currency_iso_check',
    'merchant_offers_quantities_positive_check'
)
   OR constraint_info.conrelid IN (
       to_regclass('public.demand_group_family_aliases'),
       to_regclass('public.demand_group_merge_requests'),
       to_regclass('public.demand_group_redirects'),
       to_regclass('public.demand_group_members'),
       to_regclass('public.customer_offer_selections'),
       to_regclass('public.orders')
   )
ORDER BY table_name, constraint_info.conname;

SELECT
    index_info.indrelid::regclass AS table_name,
    index_info.indexrelid::regclass AS index_name,
    index_info.indisunique AS is_unique,
    index_info.indisvalid AS is_valid,
    index_info.indisready AS is_ready,
    pg_get_indexdef(index_info.indexrelid) AS definition,
    pg_get_expr(index_info.indpred, index_info.indrelid) AS predicate
FROM pg_index AS index_info
WHERE index_info.indexrelid IN (
    to_regclass('public.idx_merchant_offers_group_merchant'),
    to_regclass('public.uq_merchant_offers_group_merchant'),
    to_regclass('public.demand_group_merge_requests_active_source_uq'),
    to_regclass('public.uq_customer_offer_selection_demand'),
    to_regclass('public.uq_orders_selection'),
    to_regclass('public.demand_embeddings_model_version_idx')
)
ORDER BY index_name;

SELECT
        policies.schemaname,
        policies.tablename,
        policies.policyname,
        policies.permissive,
        policies.roles,
        policies.cmd,
        policies.qual,
        policies.with_check
FROM pg_policies AS policies
WHERE policies.schemaname = 'public'
    AND policies.tablename IN (
            'merchant_offers',
            'demand_group_family_aliases',
            'demand_group_merge_requests',
            'demand_group_redirects',
            'offer_status_history',
            'demand_embeddings'
    )
ORDER BY policies.tablename, policies.policyname;

-- Schema-artifact status for each migration. APPLIED means all listed artifacts
-- match the expected structural contract; consult history notices and detailed
-- evidence below before treating it as a migration-runner record.
WITH checks(migration, artifact, present) AS (
    SELECT '020', 'merchant_offers.group_id is nullable UUID', EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'merchant_offers'
          AND column_name = 'group_id' AND udt_name = 'uuid' AND is_nullable = 'YES'
    )
    UNION ALL SELECT '020', 'group_id FK to demand_groups(id), ON DELETE RESTRICT, validated', EXISTS (
        SELECT 1 FROM pg_constraint AS c
        WHERE c.conname = 'merchant_offers_group_id_fkey'
          AND c.conrelid = to_regclass('public.merchant_offers')
          AND c.confrelid = to_regclass('public.demand_groups')
          AND c.contype = 'f' AND c.confdeltype = 'r' AND c.convalidated
    )
    UNION ALL SELECT '020', 'idx_merchant_offers_group_merchant exact keys', EXISTS (
        SELECT 1 FROM pg_index AS i
        WHERE i.indexrelid = to_regclass('public.idx_merchant_offers_group_merchant')
          AND i.indrelid = to_regclass('public.merchant_offers')
          AND i.indisvalid AND i.indisready AND i.indnkeyatts = 2 AND i.indnatts = 2
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'group_id'
          AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'merchant_id'
    )
    UNION ALL SELECT '020', 'uq_merchant_offers_group_merchant exact partial unique keys', EXISTS (
        SELECT 1 FROM pg_index AS i
        WHERE i.indexrelid = to_regclass('public.uq_merchant_offers_group_merchant')
          AND i.indrelid = to_regclass('public.merchant_offers')
          AND i.indisunique AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 2 AND i.indnatts = 2
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'group_id'
          AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'merchant_id'
          AND regexp_replace(lower(pg_get_expr(i.indpred, i.indrelid)), '[[:space:]()]', '', 'g')
              = 'group_idisnotnullandmerchant_idisnotnull'
    )
    UNION ALL SELECT '020', 'offers_select policy exists', EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = 'public'
          AND tablename = 'merchant_offers' AND policyname = 'offers_select' AND cmd = 'SELECT'
    )
    UNION ALL SELECT '020', 'offers_insert policy exists', EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = 'public'
          AND tablename = 'merchant_offers' AND policyname = 'offers_insert' AND cmd = 'INSERT'
    )
    UNION ALL SELECT '020', 'offers_update policy exists', EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = 'public'
          AND tablename = 'merchant_offers' AND policyname = 'offers_update' AND cmd = 'UPDATE'
    )
    UNION ALL SELECT '021', 'demands grouping columns and validated status constraint',
        (SELECT count(*) = 2 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'demands'
           AND column_name IN ('grouping_status', 'grouping_review_reason'))
        AND EXISTS (
            SELECT 1 FROM pg_constraint AS c
            WHERE c.conrelid = to_regclass('public.demands')
              AND c.conname = 'demands_grouping_status_check'
              AND c.contype = 'c' AND c.convalidated
        )
    UNION ALL SELECT '021', 'demand_group_family_aliases table with RLS', EXISTS (
        SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'demand_group_family_aliases'
          AND c.relkind IN ('r', 'p') AND c.relrowsecurity
    )
    UNION ALL SELECT '021', 'demand_group_merge_requests table', to_regclass('public.demand_group_merge_requests') IS NOT NULL
    UNION ALL SELECT '021', 'demand_group_redirects table with RLS', EXISTS (
        SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'demand_group_redirects'
          AND c.relkind IN ('r', 'p') AND c.relrowsecurity
    )
    UNION ALL SELECT '021', 'active-source merge request unique index', EXISTS (
        SELECT 1 FROM pg_index AS i
        WHERE i.indexrelid = to_regclass('public.demand_group_merge_requests_active_source_uq')
          AND i.indrelid = to_regclass('public.demand_group_merge_requests')
          AND i.indisunique AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 1 AND i.indnatts = 1
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'source_group_id'
          AND pg_get_expr(i.indpred, i.indrelid) LIKE '%request_status%'
    )
        UNION ALL SELECT '021', 'merge-request idempotency key unique constraint', EXISTS (
                SELECT 1 FROM pg_constraint AS c
                WHERE c.conrelid = to_regclass('public.demand_group_merge_requests')
                    AND c.contype IN ('p', 'u')
                    AND c.conname = 'demand_group_merge_requests_idempotency_key_key'
        )
        UNION ALL SELECT '021', 'merge-request survivor/source foreign keys',
                (SELECT count(*) = 2 FROM pg_constraint AS c
                 WHERE c.conrelid = to_regclass('public.demand_group_merge_requests')
                     AND c.contype = 'f'
                     AND c.confrelid = to_regclass('public.demand_groups')
                     AND c.conname IN ('demand_group_merge_requests_survivor_group_id_fkey',
                                                         'demand_group_merge_requests_source_group_id_fkey'))
        UNION ALL SELECT '021', 'redirect source/target and request foreign keys',
                (SELECT count(*) = 3 FROM pg_constraint AS c
                 WHERE c.conrelid = to_regclass('public.demand_group_redirects')
                     AND c.contype = 'f'
                     AND c.conname IN ('demand_group_redirects_source_group_id_fkey',
                                                         'demand_group_redirects_target_group_id_fkey',
                                                         'demand_group_redirects_merge_request_id_fkey'))
    UNION ALL SELECT '021', 'resolve_demand_group_family_v1(text,text)', to_regprocedure('public.resolve_demand_group_family_v1(text,text)') IS NOT NULL
    UNION ALL SELECT '021', 'current_demand_group_merge_snapshot_v1(uuid,uuid)', to_regprocedure('public.current_demand_group_merge_snapshot_v1(uuid,uuid)') IS NOT NULL
    UNION ALL SELECT '021', 'list_demand_group_family_candidates_v1(text,text)', to_regprocedure('public.list_demand_group_family_candidates_v1(text,text)') IS NOT NULL
    UNION ALL SELECT '021', 'resolve_demand_group_id_v1(uuid)', to_regprocedure('public.resolve_demand_group_id_v1(uuid)') IS NOT NULL
    UNION ALL SELECT '021', 'propose_demand_group_merge_v1(uuid,uuid,numeric,uuid[],text,text,text,uuid,jsonb,jsonb)', to_regprocedure('public.propose_demand_group_merge_v1(uuid,uuid,numeric,uuid[],text,text,text,uuid,jsonb,jsonb)') IS NOT NULL
    UNION ALL SELECT '021', 'approve_demand_group_merge_v1(uuid,uuid)', to_regprocedure('public.approve_demand_group_merge_v1(uuid,uuid)') IS NOT NULL
    UNION ALL SELECT '021', 'merge_demand_group_request_v1(uuid)', to_regprocedure('public.merge_demand_group_request_v1(uuid)') IS NOT NULL
    UNION ALL SELECT '021', 'create_customer_demand_v2(uuid,jsonb)', to_regprocedure('public.create_customer_demand_v2(uuid,jsonb)') IS NOT NULL
    UNION ALL SELECT '021', 'set_demand_grouping_review_v1(uuid,uuid,text)', to_regprocedure('public.set_demand_grouping_review_v1(uuid,uuid,text)') IS NOT NULL
    UNION ALL SELECT '021', 'attach_demand_to_canonical_group_v1(uuid,uuid,uuid,text,text,uuid[],jsonb,text)', to_regprocedure('public.attach_demand_to_canonical_group_v1(uuid,uuid,uuid,text,text,uuid[],jsonb,text)') IS NOT NULL
    UNION ALL SELECT '021', 'create_customer_demand_with_family_group_v1(uuid,jsonb)', to_regprocedure('public.create_customer_demand_with_family_group_v1(uuid,jsonb)') IS NOT NULL
    UNION ALL SELECT '022', 'merchant_offers quoted_quantity numeric, currency text, min_order_quantity numeric',
        (SELECT count(*) = 3 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'merchant_offers'
           AND ((column_name IN ('quoted_quantity', 'min_order_quantity') AND udt_name = 'numeric')
             OR (column_name = 'currency' AND udt_name = 'text')))
    UNION ALL SELECT '022', 'currency and positive-quantity constraints validated',
        (SELECT count(*) = 2 FROM pg_constraint AS c
         WHERE c.conrelid = to_regclass('public.merchant_offers') AND c.convalidated
           AND c.conname IN ('merchant_offers_currency_iso_check', 'merchant_offers_quantities_positive_check'))
    UNION ALL SELECT '022', 'offer_status_history has RLS enabled', EXISTS (
        SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'offer_status_history'
          AND c.relkind IN ('r', 'p') AND c.relrowsecurity
    )
    UNION ALL SELECT '022', 'lock_merchant_offer_group_v1(uuid)', to_regprocedure('public.lock_merchant_offer_group_v1(uuid)') IS NOT NULL
    UNION ALL SELECT '022', 'submit_merchant_offer_v1(uuid,uuid,text,jsonb)', to_regprocedure('public.submit_merchant_offer_v1(uuid,uuid,text,jsonb)') IS NOT NULL
    UNION ALL SELECT '022', 'revise_merchant_offer_v1(uuid,uuid,numeric,numeric)', to_regprocedure('public.revise_merchant_offer_v1(uuid,uuid,numeric,numeric)') IS NOT NULL
    UNION ALL SELECT '023', 'orders.quantity and order_items.quantity numeric',
        (SELECT count(*) = 2 FROM information_schema.columns
         WHERE table_schema = 'public' AND column_name = 'quantity'
           AND ((table_name = 'orders' AND udt_name = 'numeric')
             OR (table_name = 'order_items' AND udt_name = 'numeric')))
    UNION ALL SELECT '023', 'unique customer/demand selection index exact keys', EXISTS (
        SELECT 1 FROM pg_index AS i
        WHERE i.indexrelid = to_regclass('public.uq_customer_offer_selection_demand')
          AND i.indrelid = to_regclass('public.customer_offer_selections')
          AND i.indisunique AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 2 AND i.indnatts = 2 AND i.indpred IS NULL
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'customer_id'
          AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'demand_id'
    )
    UNION ALL SELECT '023', 'unique non-null orders.selection_id index exact definition', EXISTS (
        SELECT 1 FROM pg_index AS i
        WHERE i.indexrelid = to_regclass('public.uq_orders_selection')
          AND i.indrelid = to_regclass('public.orders')
          AND i.indisunique AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 1 AND i.indnatts = 1
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'selection_id'
          AND regexp_replace(lower(pg_get_expr(i.indpred, i.indrelid)), '[[:space:]()]', '', 'g')
              = 'selection_idisnotnull'
    )
    UNION ALL SELECT '023', 'accept_merchant_offer_v1 exact signature', to_regprocedure('public.accept_merchant_offer_v1(uuid,uuid,uuid,boolean,numeric,numeric)') IS NOT NULL
    UNION ALL SELECT '023', 'guard_offer_order_write_v1 trigger on orders', EXISTS (
        SELECT 1 FROM pg_trigger AS t
        WHERE t.tgrelid = to_regclass('public.orders')
          AND t.tgname = 'guard_offer_order_write_v1' AND NOT t.tgisinternal
    )
    UNION ALL SELECT '024', 'pgvector extension, vector type, cosine operator, vector_dims function', EXISTS (
        SELECT 1
        FROM pg_extension AS e
        JOIN pg_namespace AS ns ON ns.oid = e.extnamespace
        JOIN pg_type AS vt ON vt.typnamespace = ns.oid AND vt.typname = 'vector'
        WHERE e.extname = 'vector'
          AND EXISTS (
              SELECT 1 FROM pg_operator AS op
              WHERE op.oprnamespace = ns.oid AND op.oprname = '<=>'
                AND op.oprleft = vt.oid AND op.oprright = vt.oid
          )
          AND EXISTS (
              SELECT 1 FROM pg_proc AS f
              WHERE f.pronamespace = ns.oid AND f.proname = 'vector_dims'
                AND f.pronargs = 1 AND f.proargtypes[0] = vt.oid
          )
    )
    UNION ALL SELECT '024', 'demand_embeddings.embedding is vector(768)', EXISTS (
        SELECT 1 FROM pg_attribute AS a
        WHERE a.attrelid = to_regclass('public.demand_embeddings')
          AND a.attname = 'embedding' AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'vector(768)'
    )
    UNION ALL SELECT '024', 'demand_embeddings metadata columns and RLS',
        (SELECT count(*) = 5 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'demand_embeddings'
           AND column_name IN ('demand_id', 'embedding_model', 'embedding_version', 'input_hash', 'updated_at'))
        AND EXISTS (
            SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
              AND c.relrowsecurity
        )
    UNION ALL SELECT '024', 'demand_embeddings model/version index exact keys', EXISTS (
        SELECT 1 FROM pg_index AS i
        WHERE i.indexrelid = to_regclass('public.demand_embeddings_model_version_idx')
          AND i.indrelid = to_regclass('public.demand_embeddings')
          AND i.indisvalid AND i.indisready AND i.indnkeyatts = 2 AND i.indnatts = 2
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'embedding_model'
          AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'embedding_version'
    )
    UNION ALL SELECT '024', 'search_demand_embedding_candidates_v1 exact vector(768) argument contract', EXISTS (
        SELECT 1
        FROM pg_proc AS f
        JOIN pg_namespace AS fn ON fn.oid = f.pronamespace
        JOIN pg_extension AS e ON e.extname = 'vector'
        JOIN pg_namespace AS vn ON vn.oid = e.extnamespace
        JOIN pg_type AS vt ON vt.typnamespace = vn.oid AND vt.typname = 'vector'
        WHERE fn.nspname = 'public' AND f.proname = 'search_demand_embedding_candidates_v1'
          AND f.pronargs = 6
          AND f.proargtypes[0] = vt.oid
          AND f.proargtypes[1] = 'uuid'::regtype
          AND f.proargtypes[2] = 'integer'::regtype
          AND f.proargtypes[3] = 'double precision'::regtype
          AND f.proargtypes[4] = 'text'::regtype
          AND f.proargtypes[5] = 'text'::regtype
    )
    UNION ALL SELECT '026', 'draft diagnostics table', to_regclass('public.demand_grouping_candidate_diagnostics') IS NOT NULL
    UNION ALL SELECT '026', 'draft attachment guard function', to_regprocedure('public.guard_demand_group_attachment_v1()') IS NOT NULL
    UNION ALL SELECT '026', 'draft attachment guard trigger', EXISTS (
        SELECT 1 FROM pg_trigger AS t
        WHERE t.tgrelid = to_regclass('public.demands')
          AND t.tgname = 'guard_demand_group_attachment_v1' AND NOT t.tgisinternal
    )
), migration_status AS (
    SELECT
        migration,
        CASE
            WHEN bool_and(present) THEN 'APPLIED'
            WHEN bool_or(present) THEN 'PARTIALLY PRESENT'
            ELSE 'MISSING'
        END AS status,
        count(*) FILTER (WHERE present)::TEXT || '/' || count(*)::TEXT AS artifacts_present,
        string_agg(artifact, '; ' ORDER BY artifact) FILTER (WHERE NOT present) AS missing_artifacts
    FROM checks
    GROUP BY migration
)
SELECT migration, status, artifacts_present, COALESCE(missing_artifacts, '<none>') AS missing_artifacts
FROM migration_status
ORDER BY migration;

-- Exact migration 025 data-state check. This migration's only intended artifact
-- is the reviewed alias pair. Dynamic SQL is used only after catalog checks.
DO $headphone_alias_read_only$
DECLARE
    exact_alias BOOLEAN;
    conflicting_alias BOOLEAN;
BEGIN
    IF to_regclass('public.demand_group_family_aliases') IS NULL THEN
        RAISE NOTICE 'MIGRATION STATUS 025: UNKNOWN; prerequisite demand_group_family_aliases table is absent.';
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'demand_group_family_aliases'
          AND column_name IN ('product_name_key', 'category_path_key', 'product_family_key',
                              'category_key', 'approval_reference', 'approved')
        GROUP BY table_name HAVING count(*) = 6
    ) THEN
        RAISE NOTICE 'MIGRATION STATUS 025: UNKNOWN; alias table does not have the expected columns.';
        RETURN;
    END IF;

    EXECUTE $alias_query$
        SELECT
            EXISTS (
                SELECT 1 FROM public.demand_group_family_aliases
                WHERE product_name_key = 'wireless over ear headphones'
                  AND category_path_key = 'electronics audio wireless headphones'
                  AND product_family_key = 'wireless_headphones'
                  AND category_key = 'wireless_headphones'
                  AND approval_reference = 'phase4-compatible-pair'
                  AND approved IS TRUE
            ),
            EXISTS (
                SELECT 1 FROM public.demand_group_family_aliases
                WHERE product_name_key = 'wireless over ear headphones'
                  AND category_path_key = 'electronics audio wireless headphones'
                  AND (product_family_key IS DISTINCT FROM 'wireless_headphones'
                    OR category_key IS DISTINCT FROM 'wireless_headphones'
                    OR approved IS DISTINCT FROM TRUE)
            )
    $alias_query$
    INTO exact_alias, conflicting_alias;

    IF conflicting_alias THEN
        RAISE NOTICE 'MIGRATION STATUS 025: PARTIALLY PRESENT; conflicting canonical alias row requires manual review.';
    ELSIF exact_alias THEN
        RAISE NOTICE 'MIGRATION STATUS 025: APPLIED by schema/data evidence; exact approved alias row is present.';
    ELSE
        RAISE NOTICE 'MIGRATION STATUS 025: MISSING; exact approved alias row is absent.';
    END IF;
END;
$headphone_alias_read_only$;

-- Exact public RPC signatures, return types, security mode, effective role
-- execution privileges, and ACL entries expected by migrations 021-024.
WITH expected_rpc(rpc, expected_signature, function_oid, expected_service_role, expected_authenticated) AS (
    VALUES
        ('015', 'create_customer_demand_v1(uuid,jsonb)', to_regprocedure('public.create_customer_demand_v1(uuid,jsonb)')::oid, TRUE, FALSE),
        ('021', 'resolve_demand_group_family_v1(text,text)', to_regprocedure('public.resolve_demand_group_family_v1(text,text)')::oid, TRUE, FALSE),
        ('021', 'current_demand_group_merge_snapshot_v1(uuid,uuid)', to_regprocedure('public.current_demand_group_merge_snapshot_v1(uuid,uuid)')::oid, FALSE, FALSE),
        ('021', 'list_demand_group_family_candidates_v1(text,text)', to_regprocedure('public.list_demand_group_family_candidates_v1(text,text)')::oid, TRUE, FALSE),
        ('021', 'resolve_demand_group_id_v1(uuid)', to_regprocedure('public.resolve_demand_group_id_v1(uuid)')::oid, TRUE, TRUE),
        ('021', 'propose_demand_group_merge_v1(uuid,uuid,numeric,uuid[],text,text,text,uuid,jsonb,jsonb)', to_regprocedure('public.propose_demand_group_merge_v1(uuid,uuid,numeric,uuid[],text,text,text,uuid,jsonb,jsonb)')::oid, TRUE, FALSE),
        ('021', 'approve_demand_group_merge_v1(uuid,uuid)', to_regprocedure('public.approve_demand_group_merge_v1(uuid,uuid)')::oid, TRUE, FALSE),
        ('021', 'merge_demand_group_request_v1(uuid)', to_regprocedure('public.merge_demand_group_request_v1(uuid)')::oid, TRUE, FALSE),
        ('021', 'create_customer_demand_v2(uuid,jsonb)', to_regprocedure('public.create_customer_demand_v2(uuid,jsonb)')::oid, TRUE, FALSE),
        ('021', 'set_demand_grouping_review_v1(uuid,uuid,text)', to_regprocedure('public.set_demand_grouping_review_v1(uuid,uuid,text)')::oid, TRUE, FALSE),
        ('021', 'attach_demand_to_canonical_group_v1(uuid,uuid,uuid,text,text,uuid[],jsonb,text)', to_regprocedure('public.attach_demand_to_canonical_group_v1(uuid,uuid,uuid,text,text,uuid[],jsonb,text)')::oid, TRUE, FALSE),
        ('021', 'create_customer_demand_with_family_group_v1(uuid,jsonb)', to_regprocedure('public.create_customer_demand_with_family_group_v1(uuid,jsonb)')::oid, TRUE, FALSE),
                ('024', 'search_demand_embedding_candidates_v1(vector(768),uuid,integer,double precision,text,text)', (
                        SELECT f.oid
                        FROM pg_proc AS f
                        JOIN pg_namespace AS fn ON fn.oid = f.pronamespace
                        JOIN pg_extension AS e ON e.extname = 'vector'
                        JOIN pg_namespace AS vn ON vn.oid = e.extnamespace
                        JOIN pg_type AS vt ON vt.typnamespace = vn.oid AND vt.typname = 'vector'
                        WHERE fn.nspname = 'public'
                            AND f.proname = 'search_demand_embedding_candidates_v1'
                            AND f.pronargs = 6
                            AND f.proargtypes[0] = vt.oid
                            AND f.proargtypes[1] = 'uuid'::regtype
                            AND f.proargtypes[2] = 'integer'::regtype
                            AND f.proargtypes[3] = 'double precision'::regtype
                            AND f.proargtypes[4] = 'text'::regtype
                            AND f.proargtypes[5] = 'text'::regtype
                ), TRUE, FALSE),
        ('022', 'lock_merchant_offer_group_v1(uuid)', to_regprocedure('public.lock_merchant_offer_group_v1(uuid)')::oid, TRUE, FALSE),
        ('022', 'submit_merchant_offer_v1(uuid,uuid,text,jsonb)', to_regprocedure('public.submit_merchant_offer_v1(uuid,uuid,text,jsonb)')::oid, TRUE, FALSE),
        ('022', 'revise_merchant_offer_v1(uuid,uuid,numeric,numeric)', to_regprocedure('public.revise_merchant_offer_v1(uuid,uuid,numeric,numeric)')::oid, TRUE, FALSE),
        ('023', 'accept_merchant_offer_v1(uuid,uuid,uuid,boolean,numeric,numeric)', to_regprocedure('public.accept_merchant_offer_v1(uuid,uuid,uuid,boolean,numeric,numeric)')::oid, TRUE, FALSE)
), rpc_rows AS (
    SELECT rpc, expected_signature, function_oid, expected_service_role, expected_authenticated
    FROM expected_rpc
)
SELECT
    rpc_rows.rpc AS source_migration,
    rpc_rows.expected_signature,
    p.oid::regprocedure::text AS actual_signature,
    pg_get_function_result(p.oid) AS actual_return_type,
    p.prosecdef AS security_definer,
    COALESCE((SELECT has_function_privilege(r.oid, p.oid, 'EXECUTE')
              FROM pg_roles AS r WHERE r.rolname = 'service_role'), FALSE) AS service_role_can_execute,
    rpc_rows.expected_service_role,
    COALESCE((SELECT has_function_privilege(r.oid, p.oid, 'EXECUTE')
              FROM pg_roles AS r WHERE r.rolname = 'authenticated'), FALSE) AS authenticated_can_execute,
    rpc_rows.expected_authenticated,
    COALESCE((SELECT has_function_privilege(r.oid, p.oid, 'EXECUTE')
              FROM pg_roles AS r WHERE r.rolname = 'anon'), FALSE) AS anon_can_execute,
    (SELECT string_agg(
                CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                     ELSE pg_get_userbyid(acl.grantee) END
                || ':' || acl.privilege_type
                || CASE WHEN acl.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END,
                ', ' ORDER BY acl.grantee, acl.privilege_type
            )
     FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) AS acl
    ) AS effective_acl_entries
FROM rpc_rows
LEFT JOIN pg_proc AS p ON p.oid = rpc_rows.function_oid
ORDER BY source_migration, expected_signature;

-- pgvector extension namespace, vector type, cosine operator and dimensions
-- helper. The extension must already be installed before migration 024.
SELECT
    e.extname,
    extension_schema.nspname AS extension_schema,
    vector_type.oid::regtype::text AS vector_type,
    EXISTS (
        SELECT 1 FROM pg_operator AS op
        WHERE op.oprnamespace = extension_schema.oid AND op.oprname = '<=>'
          AND op.oprleft = vector_type.oid AND op.oprright = vector_type.oid
    ) AS has_vector_cosine_operator,
    EXISTS (
        SELECT 1 FROM pg_proc AS f
        WHERE f.pronamespace = extension_schema.oid AND f.proname = 'vector_dims'
          AND f.pronargs = 1 AND f.proargtypes[0] = vector_type.oid
    ) AS has_vector_dims_function
FROM pg_extension AS e
JOIN pg_namespace AS extension_schema ON extension_schema.oid = e.extnamespace
LEFT JOIN pg_type AS vector_type
  ON vector_type.typnamespace = extension_schema.oid AND vector_type.typname = 'vector'
WHERE e.extname = 'vector';

SELECT
    to_regclass('public.demand_embeddings') AS embeddings_table,
    format_type(embedding_column.atttypid, embedding_column.atttypmod) AS embedding_column_type,
    embedding_column.attnotnull AS embedding_not_null,
    table_info.relrowsecurity AS row_level_security_enabled,
    EXISTS (
        SELECT 1 FROM pg_roles AS r
        WHERE r.rolname = 'service_role'
          AND has_table_privilege(r.oid, table_info.oid, 'SELECT,INSERT,UPDATE')
    ) AS service_role_has_expected_table_privileges
FROM (SELECT to_regclass('public.demand_embeddings') AS oid) AS target
LEFT JOIN pg_class AS table_info ON table_info.oid = target.oid
LEFT JOIN pg_attribute AS embedding_column
  ON embedding_column.attrelid = table_info.oid
 AND embedding_column.attname = 'embedding'
 AND NOT embedding_column.attisdropped;

-- Exact prerequisite shape and non-mutating data checks for 022 and 023.
-- Counts mirror the migration preflights; large tables may require read time.
DO $offer_acceptance_data_read_only$
DECLARE
    metric_count BIGINT;
BEGIN
    IF to_regclass('public.merchant_offers') IS NOT NULL
       AND to_regclass('public.demands') IS NOT NULL
       AND to_regclass('public.demand_groups') IS NOT NULL THEN
        IF (SELECT count(*) = 4 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('merchant_offers', 'group_id'), ('merchant_offers', 'merchant_id'),
                  ('merchant_offers', 'demand_id'), ('demands', 'group_id')
              )) THEN
            EXECUTE $query$
                SELECT count(*) FROM (
                    SELECT COALESCE(offer.group_id, demand.group_id), offer.merchant_id
                    FROM public.merchant_offers AS offer
                    LEFT JOIN public.demands AS demand ON demand.id = offer.demand_id
                    WHERE COALESCE(offer.group_id, demand.group_id) IS NOT NULL
                      AND offer.merchant_id IS NOT NULL
                    GROUP BY COALESCE(offer.group_id, demand.group_id), offer.merchant_id
                    HAVING count(*) > 1
                ) AS duplicate_group_merchant_offers
            $query$ INTO metric_count;
            RAISE NOTICE '020 preflight: duplicate prospective (group_id, merchant_id) offer groups=%', metric_count;
        ELSE
            RAISE NOTICE '020 data preflight: UNKNOWN; group_id, demand_id, or merchant_id columns are missing.';
        END IF;

        IF to_regclass('public.uq_merchant_offers_group_merchant') IS NOT NULL THEN
            EXECUTE $query$
                SELECT count(*) FROM public.merchant_offers AS offer
                JOIN public.demands AS demand ON demand.id = offer.demand_id
                WHERE offer.group_id IS NOT NULL
                  AND offer.group_id IS DISTINCT FROM demand.group_id
            $query$ INTO metric_count;
            RAISE NOTICE '020 conflict check: offers whose group differs from linked demand=%', metric_count;
        END IF;

        IF (SELECT count(*) = 8 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('merchant_offers', 'currency'), ('merchant_offers', 'quoted_quantity'),
                  ('merchant_offers', 'min_order_quantity'), ('merchant_offers', 'group_id'),
                  ('merchant_offers', 'demand_id'), ('demands', 'group_id'),
                  ('demands', 'currency'), ('demand_groups', 'currency')
              )) THEN
            EXECUTE $query$
                SELECT count(*) FROM (
                    SELECT demand.group_id
                    FROM public.demands AS demand
                    JOIN public.merchant_offers AS offer ON offer.group_id = demand.group_id
                    JOIN public.demand_groups AS demand_group ON demand_group.id = demand.group_id
                    GROUP BY demand.group_id
                    HAVING count(DISTINCT demand.currency) <> 1
                        OR count(*) FILTER (WHERE demand.currency IS NULL) > 0
                        OR bool_or(demand_group.currency IS NOT NULL
                                   AND demand_group.currency <> demand.currency)
                ) AS inconsistent_group_currencies
            $query$ INTO metric_count;
            RAISE NOTICE '022 preflight: grouped-offer currency conflict groups=%', metric_count;

            EXECUTE $query$
                SELECT count(*) FROM public.merchant_offers AS offer
                LEFT JOIN public.demands AS demand ON demand.id = offer.demand_id
                LEFT JOIN public.demand_groups AS demand_group ON demand_group.id = offer.group_id
                WHERE (offer.currency IS NOT NULL AND demand.currency IS NOT NULL
                       AND offer.currency <> demand.currency)
                   OR (offer.currency IS NOT NULL AND demand_group.currency IS NOT NULL
                       AND offer.currency <> demand_group.currency)
                   OR (demand.currency IS NOT NULL AND demand_group.currency IS NOT NULL
                       AND demand.currency <> demand_group.currency)
            $query$ INTO metric_count;
            RAISE NOTICE '022 preflight: offer/demand/group currency conflicts=%', metric_count;

            EXECUTE $query$
                SELECT count(*) FROM public.merchant_offers
                WHERE (currency IS NOT NULL AND currency !~ '^[A-Z]{3}$')
                   OR (min_order_quantity IS NOT NULL AND min_order_quantity <= 0)
                   OR (quoted_quantity IS NOT NULL AND quoted_quantity <= 0)
            $query$ INTO metric_count;
            RAISE NOTICE '022 preflight: invalid existing currency or non-positive quantity rows=%', metric_count;
        ELSE
            RAISE NOTICE '022 data preflight: UNKNOWN; required currency, quantity, or group columns are missing.';
        END IF;
    ELSE
        RAISE NOTICE '020/022 data preflight: UNKNOWN; merchant_offers, demands, or demand_groups is absent.';
    END IF;

        IF to_regclass('public.demand_group_merge_requests') IS NOT NULL
             AND (SELECT count(*) = 2 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'demand_group_merge_requests'
                            AND column_name IN ('source_group_id', 'request_status')) THEN
        EXECUTE $query$
            SELECT count(*) FROM (
                SELECT source_group_id
                FROM public.demand_group_merge_requests
                WHERE request_status IN ('proposed', 'approved', 'merged')
                GROUP BY source_group_id HAVING count(*) > 1
            ) AS duplicate_active_sources
        $query$ INTO metric_count;
        RAISE NOTICE '021 preflight: duplicate active merge-request source groups=%', metric_count;
    ELSE
        RAISE NOTICE '021 preflight: demand_group_merge_requests absent; active-source duplicate check unavailable.';
    END IF;

    IF to_regclass('public.customer_offer_selections') IS NOT NULL
       AND to_regclass('public.orders') IS NOT NULL
       AND (SELECT count(*) = 3 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('customer_offer_selections', 'customer_id'),
                  ('customer_offer_selections', 'demand_id'),
                  ('orders', 'selection_id')
              )) THEN
        EXECUTE $query$
            SELECT count(*) FROM (
                SELECT customer_id, demand_id
                FROM public.customer_offer_selections
                GROUP BY customer_id, demand_id HAVING count(*) > 1
            ) AS duplicate_selections
        $query$ INTO metric_count;
        RAISE NOTICE '023 preflight: duplicate customer/demand selections=%', metric_count;

        EXECUTE $query$
            SELECT count(*) FROM (
                SELECT selection_id FROM public.orders
                WHERE selection_id IS NOT NULL
                GROUP BY selection_id HAVING count(*) > 1
            ) AS duplicate_selection_orders
        $query$ INTO metric_count;
        RAISE NOTICE '023 preflight: duplicate orders per selection=%', metric_count;
    ELSE
        RAISE NOTICE '023 preflight: UNKNOWN; customer_offer_selections or orders is absent.';
    END IF;
END;
$offer_acceptance_data_read_only$;

-- Ordering and draft warnings. Migration 025 depends on 021 but does not replace
-- 022-024. Any 026 artifacts are unexpected because 026 is draft-only.
WITH artifact_state AS (
    SELECT
        to_regclass('public.demand_group_family_aliases') IS NOT NULL AS aliases_table_present,
        to_regclass('public.demand_embeddings') IS NOT NULL AS embeddings_present,
        to_regprocedure('public.submit_merchant_offer_v1(uuid,uuid,text,jsonb)') IS NOT NULL AS migration_022_rpc_present,
        to_regprocedure('public.accept_merchant_offer_v1(uuid,uuid,uuid,boolean,numeric,numeric)') IS NOT NULL AS migration_023_rpc_present,
        (to_regclass('public.demand_grouping_candidate_diagnostics') IS NOT NULL
         OR to_regprocedure('public.guard_demand_group_attachment_v1()') IS NOT NULL
         OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'guard_demand_group_attachment_v1' AND NOT tgisinternal))
            AS migration_026_artifact_present
)
SELECT warning, evidence
FROM (
    SELECT
        'OUT_OF_ORDER_OR_INCOMPLETE_DRAFT_CHAIN' AS warning,
        '021/025 grouping artifacts exist while one or more of 022, 023, 024 is not present; 025 only depends on 021 and does not satisfy those migrations.' AS evidence
    FROM artifact_state
    WHERE aliases_table_present
      AND NOT (migration_022_rpc_present AND migration_023_rpc_present AND embeddings_present)
    UNION ALL
    SELECT
        'UNEXPECTED_026_DRAFT_ARTIFACTS',
        '026 is draft-only and must not be applied; inspect any diagnostics table, guard function, or trigger before proceeding.'
    FROM artifact_state
    WHERE migration_026_artifact_present
) AS warnings;

COMMIT;