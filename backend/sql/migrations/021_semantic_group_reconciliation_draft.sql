-- DRAFT ONLY: semantic demand-group reconciliation.
-- Requires 020_merchant_offers_group_id_draft.sql to be separately reviewed first.
-- Applying this migration adds metadata/functions and seeds reviewed exact aliases.
-- It does not update, merge, or otherwise mutate any existing demand/group/member rows.
-- Supabase applies migrations transactionally. Each SECURITY DEFINER RPC below runs
-- as one PostgreSQL statement transaction; any raised exception rolls back all writes.
-- Do not remove the explicit proposal/approval boundary or add a merge invocation here.

DO $preflight$
BEGIN
    IF to_regclass('public.demand_groups') IS NULL
       OR to_regclass('public.demand_group_members') IS NULL
       OR to_regclass('public.demands') IS NULL
       OR to_regclass('public.demand_group_reviews') IS NULL
       OR to_regclass('public.customer_offer_selections') IS NULL
       OR to_regclass('public.merchant_offers') IS NULL
       OR to_regclass('public.orders') IS NULL
    THEN
        RAISE EXCEPTION 'Semantic group reconciliation requires the checked-in grouping, selection, offer, and order tables';
    END IF;

    IF EXISTS (
        SELECT required.table_name, required.column_name
        FROM (VALUES
            ('demand_groups', 'group_status'),
            ('demand_groups', 'status'),
            ('demand_groups', 'bidding_status'),
            ('demand_groups', 'approved_at'),
            ('demand_groups', 'approved_by'),
            ('demand_groups', 'total_requested_quantity'),
            ('demand_groups', 'participating_demand_count'),
            ('demand_group_members', 'demand_id'),
            ('demand_group_members', 'membership_status'),
            ('demand_group_members', 'removed_at'),
            ('demand_group_members', 'joined_at'),
            ('demand_group_members', 'original_target_price'),
            ('demand_group_members', 'original_budget'),
            ('demands', 'group_id'),
            ('demands', 'target_quantity'),
            ('demands', 'updated_at'),
            ('demand_normalized_requirements', 'updated_at'),
            ('customer_offer_selections', 'group_id'),
            ('customer_offer_selections', 'demand_id'),
            ('merchant_offers', 'demand_id'),
            ('merchant_offers', 'group_id'),
            ('orders', 'demand_id'),
            ('orders', 'selection_id')
        ) AS required(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1
            FROM information_schema.columns actual
            WHERE actual.table_schema = 'public'
              AND actual.table_name = required.table_name
              AND actual.column_name = required.column_name
        )
    ) THEN
        RAISE EXCEPTION 'Semantic group reconciliation schema assumptions are not satisfied';
    END IF;
END;
$preflight$;

ALTER TABLE public.demands
    ADD COLUMN IF NOT EXISTS grouping_status TEXT,
    ADD COLUMN IF NOT EXISTS grouping_review_reason TEXT;

DO $grouping_status_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.demands'::regclass
          AND conname = 'demands_grouping_status_check'
    ) THEN
        ALTER TABLE public.demands
            ADD CONSTRAINT demands_grouping_status_check
            CHECK (grouping_status IS NULL OR grouping_status IN ('grouped', 'needs_review'));
    END IF;
END;
$grouping_status_constraint$;

CREATE TABLE IF NOT EXISTS public.demand_group_family_aliases (
    product_name_key TEXT NOT NULL,
    category_path_key TEXT NOT NULL,
    product_family_key TEXT NOT NULL,
    category_key TEXT NOT NULL,
    approval_reference TEXT NOT NULL,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by UUID REFERENCES public.profiles(id),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (product_name_key, category_path_key),
    CHECK (btrim(product_name_key) <> ''),
    CHECK (btrim(category_path_key) <> ''),
    CHECK (btrim(product_family_key) <> ''),
    CHECK (btrim(category_key) <> ''),
    CHECK (NOT approved OR approved_at IS NOT NULL)
);

ALTER TABLE public.demand_group_family_aliases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS demand_group_family_aliases_read ON public.demand_group_family_aliases;
CREATE POLICY demand_group_family_aliases_read
    ON public.demand_group_family_aliases FOR SELECT
    USING (auth.uid() IS NOT NULL);
DROP POLICY IF EXISTS demand_group_family_aliases_admin ON public.demand_group_family_aliases;
CREATE POLICY demand_group_family_aliases_admin
    ON public.demand_group_family_aliases FOR ALL
    USING (public.is_admin()) WITH CHECK (public.is_admin());

-- Exact aliases are scoped by the full product title and category path. In particular,
-- "Electronics" is not a general alias: it maps only with the approved headphone title.
INSERT INTO public.demand_group_family_aliases (
    product_name_key, category_path_key, product_family_key, category_key,
    approval_reference, approved, approved_at
) VALUES
    ('reliable laptop', 'computers', 'laptop', 'computer_laptops', 'phase4-compatible-pair', TRUE, now()),
    ('laptop suitable', 'computers', 'laptop', 'computer_laptops', 'phase4-compatible-pair', TRUE, now()),
    ('samsung 5g smartphone', 'mobile phones', 'samsung_smartphone', 'samsung_mobile_phones', 'phase4-compatible-pair', TRUE, now()),
    ('samsung galaxy smartphone suitable', 'electronics mobile phones samsung smartphones', 'samsung_smartphone', 'samsung_mobile_phones', 'phase4-compatible-pair', TRUE, now()),
    ('wireless over ear headphones', 'electronics', 'wireless_headphones', 'wireless_headphones', 'phase4-compatible-pair', TRUE, now()),
    ('wireless over ear headphones', 'electronics audio wireless headphones', 'wireless_headphones', 'wireless_headphones', 'phase4-compatible-pair', TRUE, now()),
    ('affordable wireless over ear headphones', 'electronics audio wireless headphones', 'wireless_headphones', 'wireless_headphones', 'phase4-compatible-pair', TRUE, now())
ON CONFLICT (product_name_key, category_path_key) DO NOTHING;

CREATE OR REPLACE FUNCTION public.resolve_demand_group_family_v1(
    p_product_name TEXT,
    p_category_path TEXT
)
RETURNS TABLE(product_family_key TEXT, category_key TEXT)
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT alias.product_family_key, alias.category_key
    FROM public.demand_group_family_aliases AS alias
    WHERE alias.approved
      AND alias.product_name_key = btrim(regexp_replace(
          regexp_replace(lower(coalesce(p_product_name, '')), '[^a-z0-9]+', ' ', 'g'),
          '\s+', ' ', 'g'
      ))
      AND alias.category_path_key = btrim(regexp_replace(
          regexp_replace(lower(coalesce(p_category_path, '')), '[^a-z0-9]+', ' ', 'g'),
          '\s+', ' ', 'g'
      ));
$$;

CREATE TABLE IF NOT EXISTS public.demand_group_merge_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE,
    survivor_group_id UUID NOT NULL REFERENCES public.demand_groups(id) ON DELETE RESTRICT,
    source_group_id UUID NOT NULL REFERENCES public.demand_groups(id) ON DELETE RESTRICT,
    expected_total_quantity NUMERIC NOT NULL CHECK (expected_total_quantity > 0),
    expected_demand_ids UUID[] NOT NULL CHECK (cardinality(expected_demand_ids) > 0),
    product_family_key TEXT NOT NULL,
    category_key TEXT NOT NULL,
    compatibility_status TEXT NOT NULL CHECK (compatibility_status = 'compatible'),
    compatibility_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposal_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(proposal_snapshot) = 'object'),
    request_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (request_status IN ('proposed', 'approved', 'rejected', 'merged')),
    requested_by UUID NOT NULL REFERENCES public.profiles(id),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by UUID REFERENCES public.profiles(id),
    approved_at TIMESTAMPTZ,
    merged_at TIMESTAMPTZ,
    merge_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (survivor_group_id <> source_group_id),
    CHECK ((request_status <> 'approved') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    CHECK ((request_status <> 'merged') OR merged_at IS NOT NULL)
);

ALTER TABLE public.demand_group_merge_requests
    ADD COLUMN IF NOT EXISTS proposal_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $active_merge_index_preflight$
BEGIN
    IF EXISTS (
        SELECT source_group_id
        FROM public.demand_group_merge_requests
        WHERE request_status IN ('proposed', 'approved', 'merged')
        GROUP BY source_group_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate active merge requests per source group require manual review';
    END IF;
END;
$active_merge_index_preflight$;

CREATE UNIQUE INDEX IF NOT EXISTS demand_group_merge_requests_active_source_uq
    ON public.demand_group_merge_requests(source_group_id)
    WHERE request_status IN ('proposed', 'approved', 'merged');

DO $active_merge_index_contract$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index AS index_info
        WHERE index_info.indexrelid = to_regclass('public.demand_group_merge_requests_active_source_uq')
          AND index_info.indrelid = 'public.demand_group_merge_requests'::regclass
          AND index_info.indisunique AND index_info.indisvalid AND index_info.indisready
          AND index_info.indnkeyatts = 1 AND index_info.indnatts = 1
          AND pg_get_indexdef(index_info.indexrelid, 1, TRUE) = 'source_group_id'
          AND pg_get_expr(index_info.indpred, index_info.indrelid) IS NOT NULL
          AND pg_get_expr(index_info.indpred, index_info.indrelid) LIKE '%request_status%'
          AND pg_get_expr(index_info.indpred, index_info.indrelid) LIKE '%proposed%'
          AND pg_get_expr(index_info.indpred, index_info.indrelid) LIKE '%approved%'
          AND pg_get_expr(index_info.indpred, index_info.indrelid) LIKE '%merged%'
    ) THEN
        RAISE EXCEPTION 'Existing active merge index does not match the required contract';
    END IF;
END;
$active_merge_index_contract$;

CREATE TABLE IF NOT EXISTS public.demand_group_redirects (
    source_group_id UUID PRIMARY KEY REFERENCES public.demand_groups(id) ON DELETE RESTRICT,
    target_group_id UUID NOT NULL REFERENCES public.demand_groups(id) ON DELETE RESTRICT,
    merge_request_id UUID NOT NULL UNIQUE REFERENCES public.demand_group_merge_requests(id) ON DELETE RESTRICT,
    merged_by UUID NOT NULL REFERENCES public.profiles(id),
    merged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_group_id <> target_group_id)
);

CREATE OR REPLACE FUNCTION public.current_demand_group_merge_snapshot_v1(
    p_survivor_group_id UUID,
    p_source_group_id UUID
)
RETURNS JSONB
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH group_ids AS (
        SELECT unnest(ARRAY[p_survivor_group_id, p_source_group_id]) AS id
    ), demand_ids AS (
        SELECT demand.id
        FROM public.demands AS demand
        WHERE demand.group_id IN (p_survivor_group_id, p_source_group_id)
    ), selection_ids AS (
        SELECT selection.id
        FROM public.customer_offer_selections AS selection
        WHERE selection.group_id IN (p_survivor_group_id, p_source_group_id)
           OR selection.demand_id IN (SELECT id FROM demand_ids)
    )
    SELECT jsonb_build_object(
        'canonical_family', (
            SELECT jsonb_build_object(
                'product_family_key', family.product_family_key,
                'category_key', family.category_key
            )
            FROM public.demand_groups AS demand_group
            CROSS JOIN LATERAL public.resolve_demand_group_family_v1(
                demand_group.product_title, demand_group.category
            ) AS family
            WHERE demand_group.id = p_survivor_group_id
        ),
        'groups', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'id', demand_group.id::TEXT,
                'created_at', to_char(demand_group.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'updated_at', to_char(demand_group.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'product_title', demand_group.product_title,
                'category', demand_group.category,
                'group_status', demand_group.group_status::TEXT,
                'status', demand_group.status::TEXT,
                'bidding_status', demand_group.bidding_status::TEXT,
                'approved_at', CASE WHEN demand_group.approved_at IS NULL THEN NULL ELSE
                    to_char(demand_group.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
                'approved_by', demand_group.approved_by::TEXT,
                'bidding_start', CASE WHEN demand_group.bidding_start IS NULL THEN NULL ELSE
                    to_char(demand_group.bidding_start AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
                'bidding_deadline', CASE WHEN demand_group.bidding_deadline IS NULL THEN NULL ELSE
                    to_char(demand_group.bidding_deadline AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
                'offers_count', trim_scale(demand_group.offers_count::NUMERIC)::TEXT,
                'lowest_offer_price', CASE WHEN demand_group.lowest_offer_price IS NULL THEN NULL ELSE
                    trim_scale(demand_group.lowest_offer_price)::TEXT END,
                'top_offer_merchant', demand_group.top_offer_merchant,
                'threshold_quantity', trim_scale(demand_group.threshold_quantity::NUMERIC)::TEXT,
                'deadline', demand_group.deadline::TEXT,
                'currency', demand_group.currency,
                'total_quantity', trim_scale(demand_group.total_quantity::NUMERIC)::TEXT,
                'total_requested_quantity', trim_scale(demand_group.total_requested_quantity::NUMERIC)::TEXT,
                'participants_count', trim_scale(demand_group.participants_count::NUMERIC)::TEXT,
                'participating_demand_count', trim_scale(demand_group.participating_demand_count::NUMERIC)::TEXT
            ) ORDER BY demand_group.id)
            FROM public.demand_groups AS demand_group
            WHERE demand_group.id IN (p_survivor_group_id, p_source_group_id)
        ), '[]'::JSONB),
        'demands', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'id', demand.id::TEXT,
                'group_id', demand.group_id::TEXT,
                'customer_id', demand.customer_id::TEXT,
                'title', demand.title,
                'category', demand.category,
                'status', demand.status::TEXT,
                'target_quantity', trim_scale(demand.target_quantity::NUMERIC)::TEXT,
                'target_price', trim_scale(demand.target_price)::TEXT,
                'maximum_budget', CASE WHEN demand.maximum_budget IS NULL THEN NULL ELSE
                    trim_scale(demand.maximum_budget)::TEXT END,
                'created_at', to_char(demand.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'updated_at', to_char(demand.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            ) ORDER BY demand.id)
            FROM public.demands AS demand
            WHERE demand.group_id IN (p_survivor_group_id, p_source_group_id)
        ), '[]'::JSONB),
        'memberships', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'group_id', member.group_id::TEXT,
                'customer_id', member.customer_id::TEXT,
                'demand_id', member.demand_id::TEXT,
                'quantity_committed', trim_scale(member.quantity_committed::NUMERIC)::TEXT,
                'joined_at', to_char(member.joined_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'original_target_price', CASE WHEN member.original_target_price IS NULL THEN NULL ELSE
                    trim_scale(member.original_target_price)::TEXT END,
                'original_budget', CASE WHEN member.original_budget IS NULL THEN NULL ELSE
                    trim_scale(member.original_budget)::TEXT END,
                'membership_status', member.membership_status::TEXT,
                'removed_at', CASE WHEN member.removed_at IS NULL THEN NULL ELSE
                    to_char(member.removed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END
            ) ORDER BY member.group_id, member.customer_id)
            FROM public.demand_group_members AS member
            WHERE member.group_id IN (p_survivor_group_id, p_source_group_id)
        ), '[]'::JSONB),
        'redirects', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'source_group_id', redirect.source_group_id::TEXT,
                'target_group_id', redirect.target_group_id::TEXT,
                'merge_request_id', redirect.merge_request_id::TEXT,
                'merged_by', redirect.merged_by::TEXT,
                'merged_at', to_char(redirect.merged_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            ) ORDER BY redirect.source_group_id)
            FROM public.demand_group_redirects AS redirect
            WHERE redirect.source_group_id IN (SELECT id FROM group_ids)
               OR redirect.target_group_id IN (SELECT id FROM group_ids)
        ), '[]'::JSONB),
        'review_ids', coalesce((
            SELECT jsonb_agg(review.id::TEXT ORDER BY review.id)
            FROM public.demand_group_reviews AS review
            WHERE review.group_id IN (SELECT id FROM group_ids)
        ), '[]'::JSONB),
        'offer_ids', coalesce((
            SELECT jsonb_agg(offer.id::TEXT ORDER BY offer.id)
            FROM public.merchant_offers AS offer
            WHERE offer.group_id IN (SELECT id FROM group_ids)
               OR offer.demand_id IN (SELECT id FROM demand_ids)
        ), '[]'::JSONB),
        'selection_ids', coalesce((
            SELECT jsonb_agg(selection.id::TEXT ORDER BY selection.id)
            FROM public.customer_offer_selections AS selection
            WHERE selection.id IN (SELECT id FROM selection_ids)
        ), '[]'::JSONB),
        'order_ids', coalesce((
            SELECT jsonb_agg(order_row.id::TEXT ORDER BY order_row.id)
            FROM public.orders AS order_row
            WHERE order_row.demand_id IN (SELECT id FROM demand_ids)
               OR order_row.selection_id IN (SELECT id FROM selection_ids)
        ), '[]'::JSONB)
    );
$$;

CREATE OR REPLACE FUNCTION public.list_demand_group_family_candidates_v1(
    p_product_family_key TEXT,
    p_category_key TEXT
)
RETURNS SETOF public.demand_groups
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT demand_group.*
    FROM public.demand_groups AS demand_group
    CROSS JOIN LATERAL public.resolve_demand_group_family_v1(
        demand_group.product_title,
        demand_group.category
    ) AS family
    WHERE family.product_family_key = p_product_family_key
      AND family.category_key = p_category_key
      AND NOT EXISTS (
          SELECT 1 FROM public.demand_group_redirects AS redirect
          WHERE redirect.source_group_id = demand_group.id
      )
    ORDER BY demand_group.created_at, demand_group.id;
$$;

ALTER TABLE public.demand_group_merge_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS demand_group_merge_requests_read ON public.demand_group_merge_requests;
CREATE POLICY demand_group_merge_requests_read
    ON public.demand_group_merge_requests FOR SELECT
    USING (public.is_admin());
DROP POLICY IF EXISTS demand_group_merge_requests_admin_write ON public.demand_group_merge_requests;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON TABLE public.demand_group_merge_requests
    FROM PUBLIC, anon, authenticated, service_role;

ALTER TABLE public.demand_group_redirects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS demand_group_redirects_read ON public.demand_group_redirects;
CREATE POLICY demand_group_redirects_read
    ON public.demand_group_redirects FOR SELECT
    USING (auth.uid() IS NOT NULL);
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON TABLE public.demand_group_redirects
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.guard_demand_group_redirect_v1()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_old_source UUID;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        v_old_source := OLD.source_group_id;
    END IF;
    IF NEW.source_group_id = NEW.target_group_id THEN
        RAISE EXCEPTION 'A group cannot redirect to itself' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.demand_group_redirects AS redirect
        WHERE redirect.source_group_id = NEW.target_group_id
          AND redirect.source_group_id IS DISTINCT FROM v_old_source
    ) OR EXISTS (
        SELECT 1 FROM public.demand_group_redirects AS redirect
        WHERE redirect.target_group_id = NEW.source_group_id
          AND redirect.source_group_id IS DISTINCT FROM v_old_source
    ) THEN
        RAISE EXCEPTION 'Group redirects must be flat and acyclic' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS demand_group_redirect_flat_guard ON public.demand_group_redirects;
CREATE TRIGGER demand_group_redirect_flat_guard
    BEFORE INSERT OR UPDATE ON public.demand_group_redirects
    FOR EACH ROW EXECUTE FUNCTION public.guard_demand_group_redirect_v1();

CREATE OR REPLACE FUNCTION public.resolve_demand_group_id_v1(p_group_id UUID)
RETURNS UUID
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_target UUID;
BEGIN
    SELECT target_group_id INTO v_target
    FROM public.demand_group_redirects
    WHERE source_group_id = p_group_id;
    IF v_target IS NULL THEN
        RETURN p_group_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.demand_group_redirects
        WHERE source_group_id = v_target
    ) THEN
        RAISE EXCEPTION 'Group redirect chain detected' USING ERRCODE = '23514';
    END IF;
    RETURN v_target;
END;
$$;

CREATE OR REPLACE FUNCTION public.guard_demand_group_merge_request_v1()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Merge requests cannot be deleted' USING ERRCODE = '42501';
    END IF;
    IF auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'Merge-request lifecycle is managed by service-role RPCs' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.request_status <> 'proposed'
           OR NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL
           OR NEW.merged_at IS NOT NULL OR NEW.merge_result <> '{}'::jsonb THEN
            RAISE EXCEPTION 'Merge requests must begin in the proposed state' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(
        NEW.id, NEW.idempotency_key, NEW.survivor_group_id, NEW.source_group_id,
        NEW.expected_total_quantity, NEW.expected_demand_ids,
        NEW.product_family_key, NEW.category_key, NEW.compatibility_status,
        NEW.compatibility_report, NEW.proposal_snapshot, NEW.requested_by, NEW.requested_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.idempotency_key, OLD.survivor_group_id, OLD.source_group_id,
        OLD.expected_total_quantity, OLD.expected_demand_ids,
        OLD.product_family_key, OLD.category_key, OLD.compatibility_status,
        OLD.compatibility_report, OLD.proposal_snapshot, OLD.requested_by, OLD.requested_at
    ) THEN
        RAISE EXCEPTION 'Merge plan, proposal snapshot, and compatibility report are immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.request_status = 'proposed' AND NEW.request_status = 'approved'
       AND NEW.approved_by IS NOT NULL AND NEW.approved_at IS NOT NULL
       AND NEW.merged_at IS NULL AND NEW.merge_result IS NOT DISTINCT FROM OLD.merge_result THEN
        RETURN NEW;
    END IF;
    IF OLD.request_status = 'proposed' AND NEW.request_status = 'rejected'
       AND NEW.approved_by IS NULL AND NEW.approved_at IS NULL
       AND NEW.merged_at IS NULL
       AND NEW.merge_result->>'status' = 'rejected'
       AND NEW.merge_result->>'reason' = 'stale_proposal' THEN
        RETURN NEW;
    END IF;
    IF OLD.request_status = 'approved' AND NEW.request_status = 'rejected'
       AND NEW.approved_by IS NOT DISTINCT FROM OLD.approved_by
       AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at
       AND NEW.merged_at IS NULL
       AND NEW.merge_result->>'status' = 'rejected'
       AND NEW.merge_result->>'reason' = 'stale_approved_proposal'
       AND NEW.merge_result->>'detail' IN ('proposal_snapshot_changed', 'normalized_revision_changed')
       AND NEW.merge_result->>'rejected_at' IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF OLD.request_status = 'approved' AND NEW.request_status = 'merged'
       AND NEW.approved_by IS NOT DISTINCT FROM OLD.approved_by
       AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at
       AND NEW.merged_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid merge-request state transition' USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS demand_group_merge_request_guard ON public.demand_group_merge_requests;
CREATE TRIGGER demand_group_merge_request_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.demand_group_merge_requests
    FOR EACH ROW EXECUTE FUNCTION public.guard_demand_group_merge_request_v1();

DROP FUNCTION IF EXISTS public.propose_demand_group_merge_v1(UUID, UUID, NUMERIC, UUID[], TEXT, TEXT, TEXT, UUID, JSONB);

CREATE OR REPLACE FUNCTION public.propose_demand_group_merge_v1(
    p_survivor_group_id UUID,
    p_source_group_id UUID,
    p_expected_total_quantity NUMERIC,
    p_expected_demand_ids UUID[],
    p_product_family_key TEXT,
    p_category_key TEXT,
    p_idempotency_key TEXT,
    p_requested_by UUID,
    p_compatibility_report JSONB,
    p_proposal_snapshot JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_survivor public.demand_groups%ROWTYPE;
    v_source public.demand_groups%ROWTYPE;
    v_survivor_family RECORD;
    v_source_family RECORD;
    v_actual_ids UUID[];
    v_actual_total NUMERIC;
    v_request public.demand_group_merge_requests%ROWTYPE;
    v_revision JSONB;
    v_current_timestamp TIMESTAMPTZ;
    v_current_normalized JSONB;
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'This operation requires the backend service role' USING ERRCODE = '42501';
    END IF;
    IF p_survivor_group_id IS NULL OR p_source_group_id IS NULL
       OR p_survivor_group_id = p_source_group_id
       OR p_expected_total_quantity IS NULL OR p_expected_total_quantity <= 0
       OR p_expected_demand_ids IS NULL OR cardinality(p_expected_demand_ids) = 0
       OR p_product_family_key IS NULL OR btrim(p_product_family_key) = ''
       OR p_category_key IS NULL OR btrim(p_category_key) = ''
       OR p_idempotency_key IS NULL OR btrim(p_idempotency_key) = ''
         OR jsonb_typeof(p_proposal_snapshot) IS DISTINCT FROM 'object'
       OR p_compatibility_report->>'status' IS DISTINCT FROM 'compatible'
       OR NOT EXISTS (
           SELECT 1 FROM public.profiles
           WHERE id = p_requested_by AND role::TEXT = 'admin'
       ) THEN
        RAISE EXCEPTION 'Merge proposal failed identity or compatibility validation' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_compatibility_report->'normalized_revisions') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Compatibility report revision snapshot must be an array' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(p_compatibility_report->'normalized_revisions') <> cardinality(p_expected_demand_ids)
       OR (
           SELECT count(DISTINCT (revision.value->>'demand_id')::UUID)
           FROM jsonb_array_elements(p_compatibility_report->'normalized_revisions') AS revision(value)
       ) <> cardinality(p_expected_demand_ids) THEN
        RAISE EXCEPTION 'Compatibility report revision snapshot must cover every demand exactly once' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM unnest(p_expected_demand_ids) AS expected(demand_id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(p_compatibility_report->'normalized_revisions') AS revision(value)
            WHERE (revision.value->>'demand_id')::UUID = expected.demand_id
        )
    ) THEN
        RAISE EXCEPTION 'Compatibility report revision snapshot must cover every demand exactly once' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_category_key || ':' || p_product_family_key, 0)
    );
    PERFORM demand_group.id
    FROM public.demand_groups AS demand_group
    WHERE demand_group.id IN (p_survivor_group_id, p_source_group_id)
    ORDER BY demand_group.id
    FOR UPDATE;
    SELECT * INTO v_survivor FROM public.demand_groups WHERE id = p_survivor_group_id;
    SELECT * INTO v_source FROM public.demand_groups WHERE id = p_source_group_id;
    IF NOT FOUND OR v_survivor.id IS NULL OR v_source.id IS NULL
       OR (v_survivor.created_at, v_survivor.id) > (v_source.created_at, v_source.id)
       OR v_survivor.group_status IS DISTINCT FROM 'Proposed'
       OR v_source.group_status IS DISTINCT FROM 'Proposed'
       OR v_survivor.status::TEXT IS DISTINCT FROM 'Forming'
       OR v_source.status::TEXT IS DISTINCT FROM 'Forming'
       OR v_survivor.bidding_status IS DISTINCT FROM 'closed'
       OR v_source.bidding_status IS DISTINCT FROM 'closed'
       OR v_survivor.approved_at IS NOT NULL OR v_source.approved_at IS NOT NULL
       OR v_survivor.approved_by IS NOT NULL OR v_source.approved_by IS NOT NULL
       OR v_survivor.bidding_start IS NOT NULL OR v_source.bidding_start IS NOT NULL
       OR v_survivor.bidding_deadline IS NOT NULL OR v_source.bidding_deadline IS NOT NULL
       OR v_survivor.offers_count <> 0 OR v_source.offers_count <> 0
       OR v_survivor.lowest_offer_price IS NOT NULL OR v_source.lowest_offer_price IS NOT NULL
       OR v_survivor.top_offer_merchant IS NOT NULL OR v_source.top_offer_merchant IS NOT NULL THEN
        RAISE EXCEPTION 'Merge proposal lifecycle or survivor preconditions failed' USING ERRCODE = '23514';
    END IF;
    PERFORM demand.id
    FROM public.demands AS demand
    WHERE demand.group_id IN (p_survivor_group_id, p_source_group_id)
    ORDER BY demand.id
    FOR UPDATE;
        PERFORM member.group_id
        FROM public.demand_group_members AS member
        WHERE member.group_id IN (p_survivor_group_id, p_source_group_id)
        ORDER BY member.group_id, member.customer_id
        FOR UPDATE;
        PERFORM normalized.demand_id
        FROM public.demand_normalized_requirements AS normalized
        WHERE normalized.demand_id = ANY(p_expected_demand_ids)
            AND normalized.extraction_version = 'v1'
        ORDER BY normalized.demand_id
        FOR UPDATE;
    FOR v_revision IN
        SELECT value FROM jsonb_array_elements(p_compatibility_report->'normalized_revisions')
    LOOP
        IF (v_revision->>'demand_id')::UUID <> ALL(p_expected_demand_ids) THEN
            RAISE EXCEPTION 'Compatibility report contains an unexpected demand' USING ERRCODE = '40001';
        END IF;
        SELECT updated_at INTO v_current_timestamp
        FROM public.demands WHERE id = (v_revision->>'demand_id')::UUID;
        IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
            NULLIF(v_revision->>'demand_updated_at', '')::TIMESTAMPTZ THEN
            RAISE EXCEPTION 'Demand changed after compatibility assessment' USING ERRCODE = '40001';
        END IF;
                SELECT normalized.updated_at, to_jsonb(normalized)
                    INTO v_current_timestamp, v_current_normalized
                FROM public.demand_normalized_requirements AS normalized
                WHERE normalized.demand_id = (v_revision->>'demand_id')::UUID
                    AND normalized.extraction_version = 'v1';
        IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
                        NULLIF(v_revision->>'normalized_updated_at', '')::TIMESTAMPTZ
                     OR jsonb_typeof(v_revision->'normalized_snapshot') IS DISTINCT FROM 'object'
                     OR v_current_normalized IS DISTINCT FROM v_revision->'normalized_snapshot' THEN
            RAISE EXCEPTION 'Normalized requirements changed after compatibility assessment' USING ERRCODE = '40001';
        END IF;
    END LOOP;
    IF p_proposal_snapshot IS DISTINCT FROM
       public.current_demand_group_merge_snapshot_v1(p_survivor_group_id, p_source_group_id) THEN
        RAISE EXCEPTION 'Proposal snapshot changed before request creation' USING ERRCODE = '40001';
    END IF;

    SELECT * INTO v_survivor_family
    FROM public.resolve_demand_group_family_v1(
        v_survivor.product_title,
        v_survivor.category
    );
    SELECT * INTO v_source_family
    FROM public.resolve_demand_group_family_v1(
        v_source.product_title,
        v_source.category
    );
    IF v_survivor_family.product_family_key IS DISTINCT FROM p_product_family_key
       OR v_source_family.product_family_key IS DISTINCT FROM p_product_family_key
       OR v_survivor_family.category_key IS DISTINCT FROM p_category_key
       OR v_source_family.category_key IS DISTINCT FROM p_category_key THEN
        RAISE EXCEPTION 'Merge proposal family/category aliases do not match both groups' USING ERRCODE = '23514';
    END IF;

    SELECT array_agg(demand.id ORDER BY demand.created_at, demand.id),
           coalesce(sum(demand.target_quantity), 0)
      INTO v_actual_ids, v_actual_total
    FROM public.demands AS demand
    WHERE demand.group_id IN (p_survivor_group_id, p_source_group_id);
    IF v_actual_ids IS DISTINCT FROM p_expected_demand_ids
       OR v_actual_total IS DISTINCT FROM p_expected_total_quantity THEN
        RAISE EXCEPTION 'Merge proposal demand snapshot is stale' USING ERRCODE = '40001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.demands AS demand
        WHERE demand.group_id IN (p_survivor_group_id, p_source_group_id)
          AND demand.status::TEXT NOT IN ('Submitted', 'Under Review')
    ) THEN
        RAISE EXCEPTION 'Only submitted or under-review demands can be proposed for merge' USING ERRCODE = '23514';
    END IF;

    SELECT * INTO v_request
    FROM public.demand_group_merge_requests
    WHERE idempotency_key = p_idempotency_key
    FOR UPDATE;
    IF FOUND THEN
        IF v_request.survivor_group_id IS DISTINCT FROM p_survivor_group_id
           OR v_request.source_group_id IS DISTINCT FROM p_source_group_id
           OR v_request.expected_total_quantity IS DISTINCT FROM p_expected_total_quantity
              OR v_request.expected_demand_ids IS DISTINCT FROM p_expected_demand_ids
              OR v_request.product_family_key IS DISTINCT FROM p_product_family_key
              OR v_request.category_key IS DISTINCT FROM p_category_key
              OR v_request.compatibility_report IS DISTINCT FROM p_compatibility_report
              OR v_request.proposal_snapshot IS DISTINCT FROM p_proposal_snapshot
              OR v_request.requested_by IS DISTINCT FROM p_requested_by THEN
            RAISE EXCEPTION 'Idempotency key was reused with a different merge plan' USING ERRCODE = '23505';
        END IF;
        RETURN to_jsonb(v_request);
    END IF;

    BEGIN
        INSERT INTO public.demand_group_merge_requests (
            idempotency_key, survivor_group_id, source_group_id,
            expected_total_quantity, expected_demand_ids,
            product_family_key, category_key, compatibility_status,
            compatibility_report, proposal_snapshot, requested_by
        ) VALUES (
            p_idempotency_key, p_survivor_group_id, p_source_group_id,
            p_expected_total_quantity, p_expected_demand_ids,
            p_product_family_key, p_category_key, 'compatible',
            coalesce(p_compatibility_report, '{}'::jsonb), p_proposal_snapshot, p_requested_by
        ) RETURNING * INTO v_request;
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_request
        FROM public.demand_group_merge_requests
        WHERE idempotency_key = p_idempotency_key
        FOR UPDATE;
        IF NOT FOUND
           OR v_request.survivor_group_id IS DISTINCT FROM p_survivor_group_id
           OR v_request.source_group_id IS DISTINCT FROM p_source_group_id
           OR v_request.expected_total_quantity IS DISTINCT FROM p_expected_total_quantity
              OR v_request.expected_demand_ids IS DISTINCT FROM p_expected_demand_ids
              OR v_request.product_family_key IS DISTINCT FROM p_product_family_key
              OR v_request.category_key IS DISTINCT FROM p_category_key
              OR v_request.compatibility_report IS DISTINCT FROM p_compatibility_report
              OR v_request.proposal_snapshot IS DISTINCT FROM p_proposal_snapshot
              OR v_request.requested_by IS DISTINCT FROM p_requested_by THEN
            RAISE;
        END IF;
    END;
    RETURN to_jsonb(v_request);
END;
$$;

CREATE OR REPLACE FUNCTION public.approve_demand_group_merge_v1(
    p_request_id UUID,
    p_approved_by UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request public.demand_group_merge_requests%ROWTYPE;
    v_survivor public.demand_groups%ROWTYPE;
    v_source public.demand_groups%ROWTYPE;
    v_survivor_family RECORD;
    v_source_family RECORD;
    v_actual_ids UUID[];
    v_actual_total NUMERIC;
    v_revision JSONB;
    v_current_timestamp TIMESTAMPTZ;
    v_current_normalized JSONB;
    v_stale_reason TEXT;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role'
       OR NOT EXISTS (
           SELECT 1 FROM public.profiles
           WHERE id = p_approved_by AND role::TEXT = 'admin'
       ) THEN
        RAISE EXCEPTION 'Merge approval requires an administrator and service role' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_request
    FROM public.demand_group_merge_requests
    WHERE id = p_request_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Merge request is not awaiting approval' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended(v_request.category_key || ':' || v_request.product_family_key, 0)
    );
    PERFORM demand_group.id
    FROM public.demand_groups AS demand_group
    WHERE demand_group.id IN (v_request.survivor_group_id, v_request.source_group_id)
    ORDER BY demand_group.id
    FOR UPDATE;
    PERFORM demand.id
    FROM public.demands AS demand
    WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ORDER BY demand.id
    FOR UPDATE;
    PERFORM member.group_id
    FROM public.demand_group_members AS member
    WHERE member.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ORDER BY member.group_id, member.customer_id
    FOR UPDATE;
    PERFORM normalized.demand_id
    FROM public.demand_normalized_requirements AS normalized
    WHERE normalized.demand_id = ANY(v_request.expected_demand_ids)
      AND normalized.extraction_version = 'v1'
    ORDER BY normalized.demand_id
    FOR UPDATE;
    SELECT * INTO v_request
    FROM public.demand_group_merge_requests
    WHERE id = p_request_id
    FOR UPDATE;
    IF NOT FOUND OR v_request.request_status <> 'proposed' THEN
        RAISE EXCEPTION 'Merge request is not awaiting approval' USING ERRCODE = '23514';
    END IF;
    IF v_request.proposal_snapshot IS DISTINCT FROM
       public.current_demand_group_merge_snapshot_v1(
           v_request.survivor_group_id, v_request.source_group_id
       ) THEN
        UPDATE public.demand_group_merge_requests
        SET request_status = 'rejected',
            merge_result = jsonb_build_object(
                'status', 'rejected',
                'reason', 'stale_proposal',
                'detail', 'proposal_snapshot_changed',
                'rejected_at', v_now
            )
        WHERE id = p_request_id
        RETURNING * INTO v_request;
        RETURN to_jsonb(v_request);
    END IF;

    SELECT * INTO v_survivor
    FROM public.demand_groups WHERE id = v_request.survivor_group_id;
    SELECT * INTO v_source
    FROM public.demand_groups WHERE id = v_request.source_group_id;
    IF NOT FOUND OR v_survivor.id IS NULL OR v_source.id IS NULL
       OR (v_survivor.created_at, v_survivor.id) > (v_source.created_at, v_source.id)
       OR v_survivor.group_status IS DISTINCT FROM 'Proposed'
       OR v_source.group_status IS DISTINCT FROM 'Proposed'
       OR v_survivor.status::TEXT IS DISTINCT FROM 'Forming'
       OR v_source.status::TEXT IS DISTINCT FROM 'Forming'
       OR v_survivor.bidding_status IS DISTINCT FROM 'closed'
       OR v_source.bidding_status IS DISTINCT FROM 'closed'
       OR v_survivor.approved_at IS NOT NULL OR v_source.approved_at IS NOT NULL
       OR v_survivor.approved_by IS NOT NULL OR v_source.approved_by IS NOT NULL
       OR v_survivor.bidding_start IS NOT NULL OR v_source.bidding_start IS NOT NULL
       OR v_survivor.bidding_deadline IS NOT NULL OR v_source.bidding_deadline IS NOT NULL
       OR v_survivor.offers_count <> 0 OR v_source.offers_count <> 0
       OR v_survivor.lowest_offer_price IS NOT NULL OR v_source.lowest_offer_price IS NOT NULL
       OR v_survivor.top_offer_merchant IS NOT NULL OR v_source.top_offer_merchant IS NOT NULL
       OR v_survivor.threshold_quantity IS DISTINCT FROM v_source.threshold_quantity
       OR v_survivor.deadline IS DISTINCT FROM v_source.deadline
       OR v_survivor.currency IS DISTINCT FROM v_source.currency THEN
        v_stale_reason := 'group_snapshot_changed';
    END IF;

    IF v_stale_reason IS NULL THEN
        SELECT * INTO v_survivor_family
        FROM public.resolve_demand_group_family_v1(v_survivor.product_title, v_survivor.category);
        SELECT * INTO v_source_family
        FROM public.resolve_demand_group_family_v1(v_source.product_title, v_source.category);
        IF v_survivor_family.product_family_key IS DISTINCT FROM v_request.product_family_key
           OR v_source_family.product_family_key IS DISTINCT FROM v_request.product_family_key
           OR v_survivor_family.category_key IS DISTINCT FROM v_request.category_key
           OR v_source_family.category_key IS DISTINCT FROM v_request.category_key THEN
            v_stale_reason := 'family_snapshot_changed';
        END IF;
    END IF;

    IF v_stale_reason IS NULL THEN
        SELECT array_agg(demand.id ORDER BY demand.created_at, demand.id),
               coalesce(sum(demand.target_quantity), 0)
          INTO v_actual_ids, v_actual_total
        FROM public.demands AS demand
        WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id);
        IF v_actual_ids IS DISTINCT FROM v_request.expected_demand_ids
           OR v_actual_total IS DISTINCT FROM v_request.expected_total_quantity THEN
            v_stale_reason := 'demand_set_or_quantity_changed';
        ELSIF EXISTS (
            SELECT 1 FROM public.demands AS demand
            WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
              AND (demand.customer_id IS NULL OR demand.target_quantity IS NULL OR demand.target_quantity <= 0
                   OR demand.status::TEXT NOT IN ('Submitted', 'Under Review'))
        ) THEN
            v_stale_reason := 'demand_eligibility_changed';
        END IF;
    END IF;

    IF v_stale_reason IS NULL
       AND (
           jsonb_typeof(v_request.compatibility_report->'normalized_revisions') IS DISTINCT FROM 'array'
           OR jsonb_array_length(v_request.compatibility_report->'normalized_revisions')
                <> cardinality(v_request.expected_demand_ids)
           OR (
               SELECT count(DISTINCT (revision.value->>'demand_id')::UUID)
               FROM jsonb_array_elements(v_request.compatibility_report->'normalized_revisions') AS revision(value)
           ) <> cardinality(v_request.expected_demand_ids)
           OR EXISTS (
               SELECT 1
               FROM unnest(v_request.expected_demand_ids) AS expected(demand_id)
               WHERE NOT EXISTS (
                   SELECT 1
                   FROM jsonb_array_elements(v_request.compatibility_report->'normalized_revisions') AS revision(value)
                   WHERE (revision.value->>'demand_id')::UUID = expected.demand_id
               )
           )
       ) THEN
        v_stale_reason := 'revision_snapshot_incomplete';
    END IF;

    IF v_stale_reason IS NULL THEN
        FOR v_revision IN
            SELECT value FROM jsonb_array_elements(v_request.compatibility_report->'normalized_revisions')
        LOOP
            IF (v_revision->>'demand_id')::UUID <> ALL(v_request.expected_demand_ids) THEN
                v_stale_reason := 'revision_snapshot_changed';
                EXIT;
            END IF;
            SELECT updated_at INTO v_current_timestamp
            FROM public.demands WHERE id = (v_revision->>'demand_id')::UUID;
            IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
                NULLIF(v_revision->>'demand_updated_at', '')::TIMESTAMPTZ THEN
                v_stale_reason := 'demand_revision_changed';
                EXIT;
            END IF;
                        SELECT normalized.updated_at, to_jsonb(normalized)
                            INTO v_current_timestamp, v_current_normalized
                        FROM public.demand_normalized_requirements AS normalized
                        WHERE normalized.demand_id = (v_revision->>'demand_id')::UUID
                            AND normalized.extraction_version = 'v1';
            IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
                                NULLIF(v_revision->>'normalized_updated_at', '')::TIMESTAMPTZ
                             OR jsonb_typeof(v_revision->'normalized_snapshot') IS DISTINCT FROM 'object'
                             OR v_current_normalized IS DISTINCT FROM v_revision->'normalized_snapshot' THEN
                v_stale_reason := 'normalized_revision_changed';
                EXIT;
            END IF;
        END LOOP;
    END IF;

    IF v_stale_reason IS NOT NULL THEN
        UPDATE public.demand_group_merge_requests
        SET request_status = 'rejected',
            merge_result = jsonb_build_object(
                'status', 'rejected',
                'reason', 'stale_proposal',
                'detail', v_stale_reason,
                'rejected_at', v_now
            )
        WHERE id = p_request_id
        RETURNING * INTO v_request;
        RETURN to_jsonb(v_request);
    END IF;

    UPDATE public.demand_group_merge_requests
    SET request_status = 'approved', approved_by = p_approved_by, approved_at = now()
    WHERE id = p_request_id
    RETURNING * INTO v_request;
    RETURN to_jsonb(v_request);
END;
$$;

CREATE OR REPLACE FUNCTION public.merge_demand_group_request_v1(p_request_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request public.demand_group_merge_requests%ROWTYPE;
    v_survivor public.demand_groups%ROWTYPE;
    v_source public.demand_groups%ROWTYPE;
    v_survivor_family RECORD;
    v_source_family RECORD;
    v_demand_ids UUID[];
    v_total NUMERIC;
    v_demand_count INTEGER;
    v_customer_count INTEGER;
    v_revision JSONB;
    v_current_timestamp TIMESTAMPTZ;
    v_current_normalized JSONB;
    v_now TIMESTAMPTZ := clock_timestamp();
    v_result JSONB;
    v_stale_reason TEXT;
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'This operation requires the backend service role' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_request
    FROM public.demand_group_merge_requests
    WHERE id = p_request_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Merge request not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_request.request_status = 'merged' THEN
        RETURN v_request.merge_result;
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended(v_request.category_key || ':' || v_request.product_family_key, 0)
    );
    PERFORM demand_group.id
    FROM public.demand_groups AS demand_group
    WHERE demand_group.id IN (v_request.survivor_group_id, v_request.source_group_id)
    ORDER BY demand_group.id
    FOR UPDATE;
    PERFORM demand.id
    FROM public.demands AS demand
    WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ORDER BY demand.id
    FOR UPDATE;
    PERFORM member.group_id
    FROM public.demand_group_members AS member
    WHERE member.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ORDER BY member.group_id, member.customer_id
    FOR UPDATE;
    PERFORM normalized.demand_id
    FROM public.demand_normalized_requirements AS normalized
    WHERE normalized.demand_id = ANY(v_request.expected_demand_ids)
      AND normalized.extraction_version = 'v1'
    ORDER BY normalized.demand_id
    FOR UPDATE;
    SELECT * INTO v_request
    FROM public.demand_group_merge_requests
    WHERE id = p_request_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Merge request not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_request.request_status = 'merged' THEN
        RETURN v_request.merge_result;
    END IF;
    IF v_request.request_status <> 'approved'
       OR v_request.approved_by IS NULL OR v_request.approved_at IS NULL THEN
        RAISE EXCEPTION 'Merge request requires explicit approval' USING ERRCODE = '42501';
    END IF;
    IF v_request.proposal_snapshot IS DISTINCT FROM
       public.current_demand_group_merge_snapshot_v1(
           v_request.survivor_group_id, v_request.source_group_id
       ) THEN
        UPDATE public.demand_group_merge_requests
        SET request_status = 'rejected',
            merge_result = jsonb_build_object(
                'status', 'rejected',
                'reason', 'stale_approved_proposal',
                'detail', 'proposal_snapshot_changed',
                'rejected_at', v_now
            )
        WHERE id = p_request_id
        RETURNING * INTO v_request;
        RETURN to_jsonb(v_request);
    END IF;

    SELECT * INTO v_survivor
    FROM public.demand_groups WHERE id = v_request.survivor_group_id;
    SELECT * INTO v_source
    FROM public.demand_groups WHERE id = v_request.source_group_id;
    IF NOT FOUND OR v_survivor.id IS NULL OR v_source.id IS NULL
       OR v_survivor.id = v_source.id THEN
        RAISE EXCEPTION 'Both distinct groups must exist' USING ERRCODE = '23503';
    END IF;
    SELECT * INTO v_survivor_family
    FROM public.resolve_demand_group_family_v1(v_survivor.product_title, v_survivor.category);
    SELECT * INTO v_source_family
    FROM public.resolve_demand_group_family_v1(v_source.product_title, v_source.category);
    IF v_survivor_family.product_family_key IS DISTINCT FROM v_request.product_family_key
       OR v_source_family.product_family_key IS DISTINCT FROM v_request.product_family_key
       OR v_survivor_family.category_key IS DISTINCT FROM v_request.category_key
       OR v_source_family.category_key IS DISTINCT FROM v_request.category_key THEN
        RAISE EXCEPTION 'Canonical product family/category changed after approval' USING ERRCODE = '40001';
    END IF;
    IF (v_survivor.created_at, v_survivor.id) > (v_source.created_at, v_source.id) THEN
        RAISE EXCEPTION 'Survivor must be the earliest-created group with UUID tie-break' USING ERRCODE = '23514';
    END IF;

    IF jsonb_typeof(v_request.compatibility_report->'normalized_revisions') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Stored compatibility revision snapshot is incomplete' USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(v_request.compatibility_report->'normalized_revisions') <> cardinality(v_request.expected_demand_ids)
       OR (
           SELECT count(DISTINCT (revision.value->>'demand_id')::UUID)
           FROM jsonb_array_elements(v_request.compatibility_report->'normalized_revisions') AS revision(value)
       ) <> cardinality(v_request.expected_demand_ids) THEN
        RAISE EXCEPTION 'Stored compatibility revision snapshot must cover every demand exactly once' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM unnest(v_request.expected_demand_ids) AS expected(demand_id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(v_request.compatibility_report->'normalized_revisions') AS revision(value)
            WHERE (revision.value->>'demand_id')::UUID = expected.demand_id
        )
    ) THEN
        RAISE EXCEPTION 'Stored compatibility revision snapshot must cover every demand exactly once' USING ERRCODE = '23514';
    END IF;
    FOR v_revision IN
        SELECT value FROM jsonb_array_elements(v_request.compatibility_report->'normalized_revisions')
    LOOP
        IF (v_revision->>'demand_id')::UUID <> ALL(v_request.expected_demand_ids) THEN
            RAISE EXCEPTION 'Stored compatibility revision snapshot contains an unexpected demand' USING ERRCODE = '23514';
        END IF;
        SELECT updated_at INTO v_current_timestamp
        FROM public.demands WHERE id = (v_revision->>'demand_id')::UUID;
        IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
            NULLIF(v_revision->>'demand_updated_at', '')::TIMESTAMPTZ THEN
            RAISE EXCEPTION 'Demand changed after merge approval' USING ERRCODE = '40001';
        END IF;
                SELECT normalized.updated_at, to_jsonb(normalized)
                    INTO v_current_timestamp, v_current_normalized
                FROM public.demand_normalized_requirements AS normalized
                WHERE normalized.demand_id = (v_revision->>'demand_id')::UUID
                    AND normalized.extraction_version = 'v1';
        IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
                        NULLIF(v_revision->>'normalized_updated_at', '')::TIMESTAMPTZ
                     OR jsonb_typeof(v_revision->'normalized_snapshot') IS DISTINCT FROM 'object'
                     OR v_current_normalized IS DISTINCT FROM v_revision->'normalized_snapshot' THEN
                        v_stale_reason := 'normalized_revision_changed';
                        EXIT;
        END IF;
    END LOOP;

    IF v_stale_reason IS NOT NULL THEN
        UPDATE public.demand_group_merge_requests
        SET request_status = 'rejected',
            merge_result = jsonb_build_object(
                'status', 'rejected',
                'reason', 'stale_approved_proposal',
                'detail', v_stale_reason,
                'rejected_at', v_now
            )
        WHERE id = p_request_id
        RETURNING * INTO v_request;
        RETURN to_jsonb(v_request);
    END IF;

    SELECT * INTO v_survivor FROM public.demand_groups WHERE id = v_request.survivor_group_id;
    SELECT * INTO v_source FROM public.demand_groups WHERE id = v_request.source_group_id;
    IF v_survivor.group_status IS DISTINCT FROM 'Proposed'
       OR v_source.group_status IS DISTINCT FROM 'Proposed'
       OR v_survivor.status::TEXT IS DISTINCT FROM 'Forming'
       OR v_source.status::TEXT IS DISTINCT FROM 'Forming'
       OR v_survivor.bidding_status IS DISTINCT FROM 'closed'
       OR v_source.bidding_status IS DISTINCT FROM 'closed'
       OR v_survivor.approved_at IS NOT NULL OR v_source.approved_at IS NOT NULL
       OR v_survivor.approved_by IS NOT NULL OR v_source.approved_by IS NOT NULL
       OR v_survivor.bidding_start IS NOT NULL OR v_source.bidding_start IS NOT NULL
       OR v_survivor.bidding_deadline IS NOT NULL OR v_source.bidding_deadline IS NOT NULL
       OR v_survivor.offers_count <> 0 OR v_source.offers_count <> 0
       OR v_survivor.lowest_offer_price IS NOT NULL OR v_source.lowest_offer_price IS NOT NULL
       OR v_survivor.top_offer_merchant IS NOT NULL OR v_source.top_offer_merchant IS NOT NULL
       OR v_survivor.threshold_quantity IS DISTINCT FROM v_source.threshold_quantity
       OR v_survivor.deadline IS DISTINCT FROM v_source.deadline
       OR v_survivor.currency IS DISTINCT FROM v_source.currency THEN
        RAISE EXCEPTION 'Lifecycle, bidding, offer, or group metadata preconditions failed' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.demand_group_redirects
        WHERE source_group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ) THEN
        RAISE EXCEPTION 'A group with an existing redirect cannot be merged as a new source or survivor' USING ERRCODE = '23514';
    END IF;

    -- Do not silently ignore unverified deployed references or FKs added outside checked-in migrations.
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns AS column_info
        WHERE column_info.table_schema = 'public'
          AND column_info.column_name = 'group_id'
          AND column_info.table_name NOT IN (
              'demands', 'demand_group_members', 'demand_group_reviews',
              'customer_offer_selections', 'merchant_offers'
          )
    ) OR EXISTS (
        SELECT 1 FROM pg_constraint AS constraint_info
        WHERE constraint_info.contype = 'f'
          AND constraint_info.confrelid = 'public.demand_groups'::regclass
          AND constraint_info.conrelid NOT IN (
              'public.demands'::regclass,
              'public.demand_group_members'::regclass,
              'public.demand_group_reviews'::regclass,
              'public.customer_offer_selections'::regclass,
              'public.merchant_offers'::regclass,
              'public.demand_group_merge_requests'::regclass,
              'public.demand_group_redirects'::regclass
          )
    ) THEN
        RAISE EXCEPTION 'Unexpected group_id reference or foreign key requires an explicit migration update' USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.demand_group_reviews
        WHERE group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ) THEN
        RAISE EXCEPTION 'Groups with review history require manual review before merge' USING ERRCODE = '23514';
    END IF;

    SELECT array_agg(demand.id ORDER BY demand.created_at, demand.id),
           coalesce(sum(demand.target_quantity), 0),
           count(*)::INTEGER,
           count(DISTINCT demand.customer_id)::INTEGER
      INTO v_demand_ids, v_total, v_demand_count, v_customer_count
    FROM public.demands AS demand
    WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id);
    IF v_demand_ids IS DISTINCT FROM v_request.expected_demand_ids
       OR v_total IS DISTINCT FROM v_request.expected_total_quantity
       OR v_demand_count = 0
       OR EXISTS (
           SELECT 1 FROM public.demands AS demand
           WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
             AND (demand.customer_id IS NULL OR demand.target_quantity IS NULL OR demand.target_quantity <= 0)
             ) OR EXISTS (
                     SELECT 1 FROM public.demands AS demand
                     WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
                         AND demand.status::TEXT NOT IN ('Submitted', 'Under Review')
       ) THEN
        RAISE EXCEPTION 'Demand set, quantity, or ownership changed after approval' USING ERRCODE = '40001';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT demand.group_id, demand.customer_id,
                   sum(demand.target_quantity) AS expected_quantity,
                   (array_agg(demand.id ORDER BY demand.created_at, demand.id))[1] AS expected_demand_id
            FROM public.demands AS demand
            WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
            GROUP BY demand.group_id, demand.customer_id
        ) AS expected
        LEFT JOIN public.demand_group_members AS member
          ON member.group_id = expected.group_id
         AND member.customer_id = expected.customer_id
        WHERE member.group_id IS NULL
           OR member.membership_status IS DISTINCT FROM 'active'
           OR member.removed_at IS NOT NULL
           OR member.demand_id IS DISTINCT FROM expected.expected_demand_id
           OR member.quantity_committed IS DISTINCT FROM expected.expected_quantity
    ) OR EXISTS (
        SELECT 1 FROM public.demand_group_members AS member
        WHERE member.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
          AND NOT EXISTS (
              SELECT 1 FROM public.demands AS demand
              WHERE demand.group_id = member.group_id
                AND demand.customer_id = member.customer_id
          )
    ) THEN
        RAISE EXCEPTION 'Membership links, representatives, or quantities are inconsistent' USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT member.customer_id
        FROM public.demand_group_members AS member
        WHERE member.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
        GROUP BY member.customer_id
        HAVING count(DISTINCT member.original_target_price) > 1
            OR count(DISTINCT member.original_budget) > 1
    ) THEN
        RAISE EXCEPTION 'Conflicting original membership metadata blocks merge' USING ERRCODE = '23514';
    END IF;

    IF v_survivor.total_quantity IS DISTINCT FROM (
           SELECT sum(target_quantity) FROM public.demands WHERE group_id = v_survivor.id
       )
       OR v_source.total_quantity IS DISTINCT FROM (
           SELECT sum(target_quantity) FROM public.demands WHERE group_id = v_source.id
       )
       OR v_survivor.total_requested_quantity IS DISTINCT FROM v_survivor.total_quantity
       OR v_source.total_requested_quantity IS DISTINCT FROM v_source.total_quantity
       OR v_survivor.participants_count IS DISTINCT FROM (
           SELECT count(DISTINCT customer_id)::INTEGER FROM public.demands WHERE group_id = v_survivor.id
       )
       OR v_source.participants_count IS DISTINCT FROM (
           SELECT count(DISTINCT customer_id)::INTEGER FROM public.demands WHERE group_id = v_source.id
       )
       OR v_survivor.participating_demand_count IS DISTINCT FROM (
           SELECT count(*)::INTEGER FROM public.demands WHERE group_id = v_survivor.id
       )
       OR v_source.participating_demand_count IS DISTINCT FROM (
           SELECT count(*)::INTEGER FROM public.demands WHERE group_id = v_source.id
       ) THEN
        RAISE EXCEPTION 'Existing group counters do not match their member demands' USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.merchant_offers AS offer
        WHERE offer.demand_id = ANY(v_demand_ids)
           OR offer.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    ) OR EXISTS (
        SELECT 1 FROM public.customer_offer_selections AS selection
        WHERE selection.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
           OR selection.demand_id = ANY(v_demand_ids)
    ) OR EXISTS (
        SELECT 1 FROM public.orders AS order_row
        WHERE order_row.demand_id = ANY(v_demand_ids)
           OR order_row.selection_id IN (
               SELECT selection.id
               FROM public.customer_offer_selections AS selection
               WHERE selection.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
                  OR selection.demand_id = ANY(v_demand_ids)
           )
    ) THEN
        RAISE EXCEPTION 'Offers, selections, or orders block automatic group merging' USING ERRCODE = '23514';
    END IF;

    -- Upsert from immutable demand quantities, not by incrementing an existing membership.
    INSERT INTO public.demand_group_members (
        group_id, customer_id, demand_id, quantity_committed,
        joined_at, original_target_price, original_budget,
        membership_status, removed_at
    )
    SELECT v_request.survivor_group_id,
           demand.customer_id,
           (array_agg(demand.id ORDER BY demand.created_at, demand.id))[1],
           sum(demand.target_quantity),
           min(member.joined_at),
           coalesce(
               max(member.original_target_price) FILTER (
                   WHERE member.group_id = v_request.survivor_group_id
               ),
               max(member.original_target_price)
           ),
           coalesce(
               max(member.original_budget) FILTER (
                   WHERE member.group_id = v_request.survivor_group_id
               ),
               max(member.original_budget)
           ),
           'active',
           NULL
    FROM public.demands AS demand
    JOIN public.demand_group_members AS member
      ON member.group_id = demand.group_id
     AND member.customer_id = demand.customer_id
    WHERE demand.group_id IN (v_request.survivor_group_id, v_request.source_group_id)
    GROUP BY demand.customer_id
    ON CONFLICT (group_id, customer_id) DO UPDATE
       SET demand_id = EXCLUDED.demand_id,
           quantity_committed = EXCLUDED.quantity_committed,
           joined_at = EXCLUDED.joined_at,
           original_target_price = EXCLUDED.original_target_price,
           original_budget = EXCLUDED.original_budget,
           membership_status = 'active',
           removed_at = NULL;

    UPDATE public.demand_group_members
    SET membership_status = 'merged', removed_at = coalesce(removed_at, v_now)
    WHERE group_id = v_request.source_group_id
      AND membership_status = 'active';

    UPDATE public.demands
    SET group_id = v_request.survivor_group_id,
        grouping_status = 'grouped',
        grouping_review_reason = NULL,
        updated_at = v_now
    WHERE group_id = v_request.source_group_id;

    -- Flatten prior aliases before adding the new source redirect; no chains are retained.
    UPDATE public.demand_group_redirects
    SET target_group_id = v_request.survivor_group_id
    WHERE target_group_id = v_request.source_group_id;
    INSERT INTO public.demand_group_redirects (
        source_group_id, target_group_id, merge_request_id, merged_by, merged_at
    ) VALUES (
        v_request.source_group_id, v_request.survivor_group_id,
        v_request.id, v_request.approved_by, v_now
    );

    UPDATE public.demand_groups AS demand_group
    SET total_quantity = counts.total_quantity,
        total_requested_quantity = counts.total_quantity,
        participants_count = counts.participant_count,
        participating_demand_count = counts.demand_count,
        compatibility_summary = demand_group.compatibility_summary || jsonb_build_object(
            'last_reconciliation', jsonb_build_object(
                'source_group_id', v_request.source_group_id,
                'merge_request_id', v_request.id,
                'merged_at', v_now,
                'expected_total_quantity', counts.total_quantity,
                'source_membership_preserved', true
            )
        ),
        updated_at = v_now
    FROM (
        SELECT coalesce(sum(target_quantity), 0) AS total_quantity,
               count(DISTINCT customer_id)::INTEGER AS participant_count,
               count(*)::INTEGER AS demand_count
        FROM public.demands
        WHERE group_id = v_request.survivor_group_id
    ) AS counts
    WHERE demand_group.id = v_request.survivor_group_id;

    UPDATE public.demand_groups AS demand_group
    SET group_status = 'Merged',
        compatibility_summary = demand_group.compatibility_summary || jsonb_build_object(
            'redirect_target_group_id', v_request.survivor_group_id,
            'merge_request_id', v_request.id,
            'merged_at', v_now,
            'tombstone', true
        ),
        updated_at = v_now
    WHERE demand_group.id = v_request.source_group_id;

    SELECT total_quantity, participants_count, participating_demand_count
      INTO v_total, v_customer_count, v_demand_count
    FROM public.demand_groups
    WHERE id = v_request.survivor_group_id;
    IF v_total IS DISTINCT FROM v_request.expected_total_quantity
       OR v_customer_count IS DISTINCT FROM (
           SELECT count(DISTINCT customer_id)::INTEGER
           FROM public.demand_group_members
           WHERE group_id = v_request.survivor_group_id AND membership_status = 'active'
       )
       OR v_demand_count IS DISTINCT FROM (
           SELECT count(*)::INTEGER FROM public.demands WHERE group_id = v_request.survivor_group_id
       )
       OR EXISTS (SELECT 1 FROM public.demands WHERE group_id = v_request.source_group_id)
       OR (SELECT group_status FROM public.demand_groups WHERE id = v_request.source_group_id) IS DISTINCT FROM 'Merged'
       OR EXISTS (
           SELECT 1 FROM public.demand_group_members
           WHERE group_id = v_request.source_group_id AND membership_status = 'active'
       ) THEN
        RAISE EXCEPTION 'Merge postconditions failed' USING ERRCODE = '23514';
    END IF;

    v_result := jsonb_build_object(
        'status', 'merged',
        'survivor_group_id', v_request.survivor_group_id,
        'source_group_id', v_request.source_group_id,
        'expected_total_quantity', v_request.expected_total_quantity,
        'demand_count', v_demand_count,
        'participant_count', v_customer_count,
        'merged_at', v_now
    );
    UPDATE public.demand_group_merge_requests
    SET request_status = 'merged', merged_at = v_now, merge_result = v_result
    WHERE id = v_request.id;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_customer_demand_v2(
    p_submission_id UUID,
    p_demand JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_saved JSONB;
    v_demand_id UUID;
    v_family RECORD;
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'This operation requires the backend service role' USING ERRCODE = '42501';
    END IF;
    v_saved := public.create_customer_demand_v1(p_submission_id, p_demand);
    v_demand_id := NULLIF(v_saved->>'id', '')::UUID;
    SELECT * INTO v_family
    FROM public.resolve_demand_group_family_v1(
        p_demand->>'title', p_demand->>'category'
    );

    IF v_family.product_family_key IS NULL OR v_family.category_key IS NULL THEN
        UPDATE public.demands
        SET grouping_status = 'needs_review',
            grouping_review_reason = 'unrecognized_product_category_alias'
        WHERE id = v_demand_id AND group_id IS NULL;
    ELSE
        UPDATE public.demands
        SET grouping_status = 'needs_review',
            grouping_review_reason = 'semantic_compatibility_pending'
        WHERE id = v_demand_id AND group_id IS NULL;
    END IF;
    SELECT to_jsonb(demand) INTO v_saved
    FROM public.demands AS demand WHERE demand.id = v_demand_id;
    RETURN v_saved;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_demand_grouping_review_v1(
    p_demand_id UUID,
    p_customer_id UUID,
    p_reason TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_saved public.demands%ROWTYPE;
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role'
       OR p_reason NOT IN (
           'candidate_compatibility_unresolved',
           'normalized_requirements_missing',
           'compatibility_assessment_failed',
           'candidate_snapshot_changed'
       ) THEN
        RAISE EXCEPTION 'Invalid grouping review request' USING ERRCODE = '42501';
    END IF;
    UPDATE public.demands
    SET grouping_status = 'needs_review', grouping_review_reason = p_reason
    WHERE id = p_demand_id AND customer_id = p_customer_id AND group_id IS NULL
    RETURNING * INTO v_saved;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Demand is not available for grouping review' USING ERRCODE = '23514';
    END IF;
    RETURN to_jsonb(v_saved);
END;
$$;

CREATE OR REPLACE FUNCTION public.attach_demand_to_canonical_group_v1(
    p_demand_id UUID,
    p_customer_id UUID,
    p_target_group_id UUID,
    p_product_family_key TEXT,
    p_category_key TEXT,
    p_expected_candidate_group_ids UUID[],
    p_candidate_snapshots JSONB,
    p_singleton_reason TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_demand public.demands%ROWTYPE;
    v_saved JSONB;
    v_family RECORD;
    v_group_id UUID;
    v_code TEXT;
    v_actual_candidate_ids UUID[];
    v_snapshot JSONB;
    v_revision JSONB;
    v_member_snapshot JSONB;
    v_member public.demand_group_members%ROWTYPE;
    v_current_timestamp TIMESTAMPTZ;
    v_current_normalized JSONB;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'This operation requires the backend service role' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_category_key || ':' || p_product_family_key, 0)
    );
    SELECT * INTO v_demand
    FROM public.demands
    WHERE id = p_demand_id AND customer_id = p_customer_id
    FOR UPDATE;
    IF NOT FOUND OR v_demand.group_id IS NOT NULL
       OR v_demand.status::TEXT NOT IN ('Submitted', 'Under Review') THEN
        RAISE EXCEPTION 'Demand is not eligible for canonical grouping' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO v_family
    FROM public.resolve_demand_group_family_v1(v_demand.title, v_demand.category);
    IF v_family.product_family_key IS DISTINCT FROM p_product_family_key
       OR v_family.category_key IS DISTINCT FROM p_category_key THEN
        RAISE EXCEPTION 'Demand canonical family changed during assessment' USING ERRCODE = '40001';
    END IF;

    PERFORM demand_group.id
    FROM public.demand_groups AS demand_group
    WHERE demand_group.id = ANY(coalesce(p_expected_candidate_group_ids, '{}'::UUID[]))
    ORDER BY demand_group.id
    FOR UPDATE;
    PERFORM demand.id
    FROM public.demands AS demand
    WHERE demand.group_id = ANY(coalesce(p_expected_candidate_group_ids, '{}'::UUID[]))
    ORDER BY demand.id
    FOR UPDATE;
    PERFORM member.group_id
    FROM public.demand_group_members AS member
    WHERE member.group_id = ANY(coalesce(p_expected_candidate_group_ids, '{}'::UUID[]))
    ORDER BY member.group_id, member.customer_id
    FOR UPDATE;
    PERFORM normalized.id
    FROM public.demand_normalized_requirements AS normalized
    JOIN public.demands AS demand ON demand.id = normalized.demand_id
    WHERE demand.group_id = ANY(coalesce(p_expected_candidate_group_ids, '{}'::UUID[]))
      AND normalized.extraction_version = 'v1'
    ORDER BY normalized.id
    FOR UPDATE;

    SELECT coalesce(array_agg(demand_group.id ORDER BY demand_group.id), '{}'::UUID[])
      INTO v_actual_candidate_ids
    FROM public.demand_groups AS demand_group
    CROSS JOIN LATERAL public.resolve_demand_group_family_v1(
        demand_group.product_title, demand_group.category
    ) AS family
    WHERE family.product_family_key = p_product_family_key
      AND family.category_key = p_category_key
      AND NOT EXISTS (
          SELECT 1 FROM public.demand_group_redirects AS redirect
          WHERE redirect.source_group_id = demand_group.id
      );
    IF v_actual_candidate_ids IS DISTINCT FROM coalesce(p_expected_candidate_group_ids, '{}'::UUID[]) THEN
        RAISE EXCEPTION 'Candidate group set changed during compatibility assessment' USING ERRCODE = '40001';
    END IF;

    IF jsonb_typeof(p_candidate_snapshots) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Candidate snapshot is incomplete' USING ERRCODE = '40001';
    END IF;
    IF jsonb_array_length(p_candidate_snapshots) <> cardinality(v_actual_candidate_ids) THEN
        RAISE EXCEPTION 'Candidate snapshot is incomplete' USING ERRCODE = '40001';
    END IF;
    IF (
        SELECT count(DISTINCT (snapshot.value->>'group_id')::UUID)
        FROM jsonb_array_elements(p_candidate_snapshots) AS snapshot(value)
    ) <> cardinality(v_actual_candidate_ids)
       OR EXISTS (
           SELECT 1
           FROM unnest(v_actual_candidate_ids) AS expected(group_id)
           WHERE NOT EXISTS (
               SELECT 1
               FROM jsonb_array_elements(p_candidate_snapshots) AS snapshot(value)
               WHERE (snapshot.value->>'group_id')::UUID = expected.group_id
           )
       ) THEN
        RAISE EXCEPTION 'Candidate snapshot must cover every candidate group exactly once' USING ERRCODE = '40001';
    END IF;
    FOR v_snapshot IN SELECT value FROM jsonb_array_elements(p_candidate_snapshots)
    LOOP
        IF NOT (v_snapshot->>'group_id')::UUID = ANY(v_actual_candidate_ids) THEN
            RAISE EXCEPTION 'Candidate snapshot contains an unexpected group' USING ERRCODE = '40001';
        END IF;
        SELECT updated_at INTO v_current_timestamp
        FROM public.demand_groups
        WHERE id = (v_snapshot->>'group_id')::UUID;
        IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
            NULLIF(v_snapshot->>'group_updated_at', '')::TIMESTAMPTZ THEN
            RAISE EXCEPTION 'Candidate group changed during compatibility assessment' USING ERRCODE = '40001';
        END IF;
        IF jsonb_typeof(v_snapshot->'demand_ids') IS DISTINCT FROM 'array'
           OR jsonb_array_length(v_snapshot->'demand_ids') <> (
               SELECT count(*) FROM public.demands
               WHERE group_id = (v_snapshot->>'group_id')::UUID
           )
           OR (
               SELECT count(DISTINCT demand_id.value::UUID)
               FROM jsonb_array_elements_text(v_snapshot->'demand_ids') AS demand_id(value)
           ) <> (
               SELECT count(*) FROM public.demands
               WHERE group_id = (v_snapshot->>'group_id')::UUID
           )
           OR EXISTS (
               SELECT 1 FROM public.demands AS demand
               WHERE demand.group_id = (v_snapshot->>'group_id')::UUID
                 AND NOT EXISTS (
                     SELECT 1 FROM jsonb_array_elements_text(v_snapshot->'demand_ids') AS demand_id(value)
                     WHERE demand_id.value::UUID = demand.id
                 )
           ) THEN
            RAISE EXCEPTION 'Candidate demand set changed during compatibility assessment' USING ERRCODE = '40001';
        END IF;
        IF jsonb_typeof(v_snapshot->'revisions') IS DISTINCT FROM 'array'
           OR jsonb_array_length(v_snapshot->'revisions') <> (
               SELECT count(*) FROM public.demands
               WHERE group_id = (v_snapshot->>'group_id')::UUID
           )
           OR (
               SELECT count(DISTINCT revision.value->>'demand_id')
               FROM jsonb_array_elements(v_snapshot->'revisions') AS revision(value)
           ) <> (
               SELECT count(*) FROM public.demands
               WHERE group_id = (v_snapshot->>'group_id')::UUID
           ) THEN
            RAISE EXCEPTION 'Candidate revision snapshot is incomplete' USING ERRCODE = '40001';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.demands AS demand
            WHERE demand.group_id = (v_snapshot->>'group_id')::UUID
              AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements(v_snapshot->'revisions') AS revision(value)
                  WHERE (revision.value->>'demand_id')::UUID = demand.id
              )
        ) THEN
            RAISE EXCEPTION 'Candidate revision snapshot must cover each demand exactly once' USING ERRCODE = '40001';
        END IF;
        FOR v_revision IN SELECT value FROM jsonb_array_elements(v_snapshot->'revisions')
        LOOP
            SELECT updated_at INTO v_current_timestamp
            FROM public.demands WHERE id = (v_revision->>'demand_id')::UUID;
            IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
                NULLIF(v_revision->>'demand_updated_at', '')::TIMESTAMPTZ THEN
                RAISE EXCEPTION 'Candidate demand changed during compatibility assessment' USING ERRCODE = '40001';
            END IF;
                        SELECT normalized.updated_at,
                                     to_jsonb(normalized) - ARRAY['created_at', 'updated_at', 'reviewed_at']
                        INTO v_current_timestamp, v_current_normalized
            FROM public.demand_normalized_requirements
                                AS normalized
            WHERE demand_id = (v_revision->>'demand_id')::UUID
              AND extraction_version = 'v1';
            IF NOT FOUND OR v_current_timestamp IS DISTINCT FROM
                                NULLIF(v_revision->>'normalized_updated_at', '')::TIMESTAMPTZ
                             OR v_current_normalized IS DISTINCT FROM v_revision->'normalized_snapshot' THEN
                RAISE EXCEPTION 'Candidate normalized requirements changed during assessment' USING ERRCODE = '40001';
            END IF;
        END LOOP;
        IF jsonb_typeof(v_snapshot->'memberships') IS DISTINCT FROM 'array'
           OR jsonb_array_length(v_snapshot->'memberships') <> (
               SELECT count(*) FROM public.demand_group_members
               WHERE group_id = (v_snapshot->>'group_id')::UUID
           )
           OR (
               SELECT count(DISTINCT membership.value->>'customer_id')
               FROM jsonb_array_elements(v_snapshot->'memberships') AS membership(value)
           ) <> (
               SELECT count(*) FROM public.demand_group_members
               WHERE group_id = (v_snapshot->>'group_id')::UUID
           ) THEN
            RAISE EXCEPTION 'Candidate membership set changed during compatibility assessment' USING ERRCODE = '40001';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.demand_group_members AS member
            WHERE member.group_id = (v_snapshot->>'group_id')::UUID
              AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements(v_snapshot->'memberships') AS membership(value)
                  WHERE (membership.value->>'customer_id')::UUID = member.customer_id
              )
        ) THEN
            RAISE EXCEPTION 'Candidate membership snapshot must cover each member exactly once' USING ERRCODE = '40001';
        END IF;
        FOR v_member_snapshot IN
            SELECT value FROM jsonb_array_elements(v_snapshot->'memberships')
        LOOP
            SELECT * INTO v_member
            FROM public.demand_group_members
            WHERE group_id = (v_snapshot->>'group_id')::UUID
              AND customer_id = (v_member_snapshot->>'customer_id')::UUID;
            IF NOT FOUND
               OR v_member.demand_id::TEXT IS DISTINCT FROM v_member_snapshot->>'demand_id'
               OR v_member.quantity_committed IS DISTINCT FROM (v_member_snapshot->>'quantity_committed')::NUMERIC
               OR v_member.membership_status IS DISTINCT FROM v_member_snapshot->>'membership_status'
               OR v_member.removed_at IS DISTINCT FROM
                (CASE WHEN jsonb_typeof(v_member_snapshot->'removed_at') = 'null'
                     THEN NULL ELSE (v_member_snapshot->>'removed_at')::TIMESTAMPTZ END)
               OR v_member.joined_at IS DISTINCT FROM (v_member_snapshot->>'joined_at')::TIMESTAMPTZ
               OR v_member.original_target_price IS DISTINCT FROM
                (CASE WHEN jsonb_typeof(v_member_snapshot->'original_target_price') = 'null'
                     THEN NULL ELSE (v_member_snapshot->>'original_target_price')::NUMERIC END)
               OR v_member.original_budget IS DISTINCT FROM
                (CASE WHEN jsonb_typeof(v_member_snapshot->'original_budget') = 'null'
                     THEN NULL ELSE (v_member_snapshot->>'original_budget')::NUMERIC END) THEN
                RAISE EXCEPTION 'Candidate membership changed during compatibility assessment' USING ERRCODE = '40001';
            END IF;
        END LOOP;
    END LOOP;

    IF p_target_group_id IS NULL THEN
        IF p_singleton_reason IS DISTINCT FROM 'no_compatible_existing_group' THEN
            RAISE EXCEPTION 'A standalone group requires a fully rejected compatible-family candidate set' USING ERRCODE = '23514';
        END IF;
        v_code := 'GRP-' || upper(replace(gen_random_uuid()::TEXT, '-', ''));
        INSERT INTO public.demand_groups (
            group_code, group_reference, normalized_product_type,
            product_title, category, currency, threshold_quantity, group_status,
            compatibility_summary
        ) VALUES (
            v_code, v_code, p_product_family_key,
            v_demand.title, v_demand.category, v_demand.currency, 1, 'Proposed',
            jsonb_build_object(
                'basis', 'approved exact product/category alias',
                'compatibility_gate', 'Python semantic assessment'
            )
        ) RETURNING id INTO v_group_id;
    ELSE
        IF p_singleton_reason IS NOT NULL THEN
            RAISE EXCEPTION 'An attached demand cannot include a singleton rejection reason' USING ERRCODE = '23514';
        END IF;
        IF NOT p_target_group_id = ANY(v_actual_candidate_ids) THEN
            RAISE EXCEPTION 'Target group was not in the assessed candidate set' USING ERRCODE = '23514';
        END IF;
        v_group_id := p_target_group_id;
        IF NOT EXISTS (
            SELECT 1 FROM public.demand_groups AS demand_group
            WHERE demand_group.id = v_group_id
              AND demand_group.group_status = 'Proposed'
              AND demand_group.status::TEXT = 'Forming'
              AND demand_group.bidding_status = 'closed'
              AND demand_group.bidding_start IS NULL
              AND demand_group.bidding_deadline IS NULL
              AND demand_group.approved_at IS NULL
              AND demand_group.approved_by IS NULL
              AND demand_group.offers_count = 0
              AND demand_group.lowest_offer_price IS NULL
              AND demand_group.top_offer_merchant IS NULL
              AND demand_group.total_quantity IS NOT DISTINCT FROM (
                  SELECT coalesce(sum(demand.target_quantity), 0)
                  FROM public.demands AS demand WHERE demand.group_id = demand_group.id
              )
              AND demand_group.total_requested_quantity IS NOT DISTINCT FROM demand_group.total_quantity
              AND demand_group.participants_count = (
                  SELECT count(DISTINCT demand.customer_id)::INTEGER
                  FROM public.demands AS demand WHERE demand.group_id = demand_group.id
              )
              AND demand_group.participating_demand_count = (
                  SELECT count(*)::INTEGER
                  FROM public.demands AS demand WHERE demand.group_id = demand_group.id
              )
              AND (demand_group.currency IS NULL OR demand_group.currency = v_demand.currency)
              AND NOT EXISTS (
                  SELECT 1 FROM public.demands AS demand
                  WHERE demand.group_id = demand_group.id
                    AND demand.currency IS DISTINCT FROM v_demand.currency
              )
              AND NOT EXISTS (
                  SELECT 1 FROM public.demand_group_reviews AS review
                  WHERE review.group_id = demand_group.id
              )
        ) THEN
            RAISE EXCEPTION 'Target group lifecycle or review state blocks attachment' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.merchant_offers AS offer
            WHERE offer.group_id = v_group_id
               OR offer.demand_id IN (
                   SELECT demand.id FROM public.demands AS demand WHERE demand.group_id = v_group_id
               )
        ) THEN
            RAISE EXCEPTION 'Offers block automatic attachment to an existing group' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT demand.customer_id,
                   sum(demand.target_quantity) AS expected_quantity,
                   (array_agg(demand.id ORDER BY demand.created_at, demand.id))[1] AS expected_demand_id
            FROM public.demands AS demand
            WHERE demand.group_id = v_group_id
            GROUP BY demand.customer_id
        ) AS expected
        LEFT JOIN public.demand_group_members AS member
          ON member.group_id = v_group_id AND member.customer_id = expected.customer_id
        WHERE member.group_id IS NULL
           OR member.membership_status IS DISTINCT FROM 'active'
               OR member.removed_at IS NOT NULL
               OR member.demand_id IS DISTINCT FROM expected.expected_demand_id
               OR member.quantity_committed IS DISTINCT FROM expected.expected_quantity
    ) THEN
        RAISE EXCEPTION 'Existing group membership is inconsistent' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.demand_group_members AS member
        WHERE member.group_id = v_group_id
          AND NOT EXISTS (
              SELECT 1 FROM public.demands AS demand
              WHERE demand.group_id = member.group_id
                AND demand.customer_id = member.customer_id
          )
    ) THEN
        RAISE EXCEPTION 'Existing group membership has no demand record' USING ERRCODE = '23514';
    END IF;

    UPDATE public.demands
    SET group_id = v_group_id,
        grouping_status = 'grouped',
        grouping_review_reason = NULL,
        updated_at = v_now
    WHERE id = p_demand_id AND customer_id = p_customer_id AND group_id IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Demand assignment changed concurrently' USING ERRCODE = '40001';
    END IF;

    INSERT INTO public.demand_group_members AS existing_member (
        group_id, customer_id, demand_id, quantity_committed,
        joined_at, original_target_price, original_budget,
        membership_status, removed_at
    )
    SELECT v_group_id,
           demand.customer_id,
           (array_agg(demand.id ORDER BY demand.created_at, demand.id))[1],
           sum(demand.target_quantity),
           min(coalesce(member.joined_at, demand.created_at)),
           (array_agg(demand.target_price ORDER BY demand.created_at, demand.id))[1],
           (array_agg(demand.maximum_budget ORDER BY demand.created_at, demand.id))[1],
           'active',
           NULL
    FROM public.demands AS demand
    LEFT JOIN public.demand_group_members AS member
      ON member.group_id = v_group_id AND member.customer_id = demand.customer_id
    WHERE demand.group_id = v_group_id
    GROUP BY demand.customer_id
    ON CONFLICT (group_id, customer_id) DO UPDATE
       SET demand_id = EXCLUDED.demand_id,
           quantity_committed = EXCLUDED.quantity_committed,
           joined_at = least(existing_member.joined_at, EXCLUDED.joined_at),
           membership_status = 'active',
           removed_at = NULL;

    UPDATE public.demand_groups AS demand_group
    SET total_quantity = counts.total_quantity,
        total_requested_quantity = counts.total_quantity,
        participants_count = counts.participant_count,
        participating_demand_count = counts.demand_count,
        currency = v_demand.currency,
        updated_at = v_now
    FROM (
        SELECT coalesce(sum(target_quantity), 0) AS total_quantity,
               count(DISTINCT customer_id)::INTEGER AS participant_count,
               count(*)::INTEGER AS demand_count
        FROM public.demands WHERE group_id = v_group_id
    ) AS counts
    WHERE demand_group.id = v_group_id;

    SELECT to_jsonb(demand) INTO v_saved
    FROM public.demands AS demand WHERE demand.id = p_demand_id;
    RETURN v_saved;
END;
$$;

-- Keep legacy callers on the canonical, compatibility-gated path.
CREATE OR REPLACE FUNCTION public.create_customer_demand_with_family_group_v1(
    p_submission_id UUID,
    p_demand JSONB
)
RETURNS JSONB
LANGUAGE SQL
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT public.create_customer_demand_v2(p_submission_id, p_demand);
$$;

REVOKE ALL ON FUNCTION public.resolve_demand_group_family_v1(TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.list_demand_group_family_candidates_v1(TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.current_demand_group_merge_snapshot_v1(UUID, UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.propose_demand_group_merge_v1(UUID, UUID, NUMERIC, UUID[], TEXT, TEXT, TEXT, UUID, JSONB, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.approve_demand_group_merge_v1(UUID, UUID)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.merge_demand_group_request_v1(UUID)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.resolve_demand_group_id_v1(UUID)
    FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.create_customer_demand_v2(UUID, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.set_demand_grouping_review_v1(UUID, UUID, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.attach_demand_to_canonical_group_v1(UUID, UUID, UUID, TEXT, TEXT, UUID[], JSONB, TEXT)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.resolve_demand_group_family_v1(TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.list_demand_group_family_candidates_v1(TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.propose_demand_group_merge_v1(UUID, UUID, NUMERIC, UUID[], TEXT, TEXT, TEXT, UUID, JSONB, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.approve_demand_group_merge_v1(UUID, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.merge_demand_group_request_v1(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.resolve_demand_group_id_v1(UUID) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.create_customer_demand_v2(UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.set_demand_grouping_review_v1(UUID, UUID, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.attach_demand_to_canonical_group_v1(UUID, UUID, UUID, TEXT, TEXT, UUID[], JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.create_customer_demand_with_family_group_v1(UUID, JSONB) TO service_role;

NOTIFY pgrst, 'reload schema';
