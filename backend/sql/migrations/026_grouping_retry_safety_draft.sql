-- One-shot Supabase migration: reviewed-demand grouping diagnostics and safeguards.
-- Requires the reviewed grouping, demand, offer, selection, and order migrations.
-- This migration does not rewrite existing demand, group, or membership data.
-- Recovery: on any preflight or postcondition failure, the transaction rolls back.
-- After a successful deployment, stop retries and preserve/export diagnostic rows
-- before any manual rollback; remove the trigger before its function and retain the
-- diagnostics table until its records are no longer needed for review.

BEGIN;

DO $grouping_retry_preflight$
BEGIN
    IF to_regclass('public.demands') IS NULL
       OR to_regclass('public.demand_groups') IS NULL
       OR to_regclass('public.demand_group_members') IS NULL
       OR to_regclass('public.merchant_offers') IS NULL
       OR to_regclass('public.customer_offer_selections') IS NULL
       OR to_regclass('public.customer_offer_eligibility') IS NULL
       OR to_regclass('public.offer_comparison_history') IS NULL
       OR to_regclass('public.orders') IS NULL THEN
        RAISE EXCEPTION 'Grouping retry safety requires the existing demand, group, offer, selection, and order tables';
    END IF;

    IF EXISTS (
        SELECT required.table_name, required.column_name
        FROM (VALUES
            ('demands', 'id'),
            ('demands', 'group_id'),
            ('demands', 'customer_id'),
            ('demands', 'target_quantity'),
            ('demands', 'status'),
            ('demands', 'grouping_status'),
            ('demand_groups', 'id'),
            ('demand_group_members', 'demand_id'),
            ('merchant_offers', 'demand_id'),
            ('customer_offer_selections', 'id'),
            ('customer_offer_selections', 'demand_id'),
            ('customer_offer_eligibility', 'demand_id'),
            ('offer_comparison_history', 'demand_id'),
            ('orders', 'demand_id'),
            ('orders', 'selection_id')
        ) AS required(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1
            FROM information_schema.columns AS actual
            WHERE actual.table_schema = 'public'
              AND actual.table_name = required.table_name
              AND actual.column_name = required.column_name
        )
    ) THEN
        RAISE EXCEPTION 'Grouping retry safety schema assumptions are not satisfied';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION 'Grouping retry safety requires service_role, anon, and authenticated roles';
    END IF;

    IF to_regclass('public.demand_grouping_candidate_diagnostics') IS NOT NULL THEN
        RAISE EXCEPTION 'Existing grouping diagnostics object requires explicit schema review';
    END IF;

    IF to_regprocedure('public.guard_demand_group_attachment_v1()') IS NOT NULL THEN
        RAISE EXCEPTION 'Existing grouping attachment guard function requires explicit schema review';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_trigger AS trigger_info
        WHERE trigger_info.tgrelid = 'public.demands'::regclass
          AND trigger_info.tgname = 'guard_demand_group_attachment_v1'
          AND NOT trigger_info.tgisinternal
    ) THEN
        RAISE EXCEPTION 'Existing grouping attachment trigger requires explicit schema review';
    END IF;
END;
$grouping_retry_preflight$;

CREATE TABLE public.demand_grouping_candidate_diagnostics (
    demand_id UUID NOT NULL REFERENCES public.demands(id) ON DELETE CASCADE,
    candidate_group_id UUID NOT NULL REFERENCES public.demand_groups(id) ON DELETE CASCADE,
    candidate_demand_ids UUID[] NOT NULL DEFAULT '{}'::UUID[],
    cosine_distance DOUBLE PRECISION CHECK (cosine_distance IS NULL OR cosine_distance BETWEEN 0 AND 2),
    assessment_status TEXT NOT NULL CHECK (
        assessment_status IN ('compatible', 'rejected', 'needs_review', 'ineligible')
    ),
    reason_codes JSONB NOT NULL DEFAULT '[]'::JSONB CHECK (jsonb_typeof(reason_codes) = 'array'),
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (demand_id, candidate_group_id)
);

ALTER TABLE public.demand_grouping_candidate_diagnostics ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.demand_grouping_candidate_diagnostics
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.demand_grouping_candidate_diagnostics TO service_role;

DO $grouping_diagnostics_acl_contract$
DECLARE
    v_table_oid OID := to_regclass('public.demand_grouping_candidate_diagnostics');
    v_service_role_oid OID;
BEGIN
    SELECT oid INTO STRICT v_service_role_oid FROM pg_roles WHERE rolname = 'service_role';

    IF NOT EXISTS (
        SELECT 1 FROM pg_class AS relation
        WHERE relation.oid = v_table_oid AND relation.relrowsecurity
    ) OR EXISTS (
        SELECT 1 FROM pg_policy AS policy WHERE policy.polrelid = v_table_oid
    ) THEN
        RAISE EXCEPTION 'Diagnostics table must have RLS enabled and no permissive policies';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        CROSS JOIN LATERAL aclexplode(
            COALESCE(relation.relacl, acldefault('r', relation.relowner))
        ) AS privilege
        WHERE relation.oid = v_table_oid
          AND (privilege.grantor <> relation.relowner
               OR privilege.grantee NOT IN (relation.relowner, v_service_role_oid))
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS relation
        CROSS JOIN LATERAL aclexplode(relation.relacl) AS privilege
        WHERE relation.oid = v_table_oid
          AND privilege.grantee = v_service_role_oid
          AND (privilege.privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE')
               OR privilege.is_grantable)
    ) OR (SELECT count(*)
          FROM pg_class AS relation
          CROSS JOIN LATERAL aclexplode(relation.relacl) AS privilege
          WHERE relation.oid = v_table_oid
            AND privilege.grantee = v_service_role_oid
            AND privilege.grantor = relation.relowner
            AND privilege.privilege_type IN ('SELECT', 'INSERT', 'UPDATE')
            AND NOT privilege.is_grantable) <> 3 THEN
        RAISE EXCEPTION 'Diagnostics table ACL must be owner plus non-grantable service_role SELECT, INSERT, UPDATE';
    END IF;
END;
$grouping_diagnostics_acl_contract$;

CREATE FUNCTION public.guard_demand_group_attachment_v1()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.group_id IS NOT NULL THEN
            RAISE EXCEPTION 'Grouped demands must be created ungrouped and assigned through the canonical attachment RPC'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.group_id IS DISTINCT FROM OLD.group_id
       AND auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'Only the backend service role may change a demand group'
            USING ERRCODE = '42501';
    END IF;

    IF OLD.group_id IS NULL AND NEW.group_id IS NOT NULL THEN
        IF NEW.customer_id IS NULL
           OR NEW.target_quantity IS NULL
           OR NEW.target_quantity <= 0
           OR NEW.status::TEXT NOT IN ('Submitted', 'Under Review')
           OR NEW.grouping_status IS DISTINCT FROM 'grouped' THEN
            RAISE EXCEPTION 'Demand lifecycle or ownership blocks automatic grouping'
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1 FROM public.demand_group_members AS member
            WHERE member.demand_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM public.merchant_offers AS offer
            WHERE offer.demand_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM public.customer_offer_selections AS selection
            WHERE selection.demand_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM public.customer_offer_eligibility AS eligibility
            WHERE eligibility.demand_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM public.offer_comparison_history AS comparison
            WHERE comparison.demand_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM public.orders AS order_row
            WHERE order_row.demand_id = OLD.id
               OR order_row.selection_id IN (
                   SELECT selection.id
                   FROM public.customer_offer_selections AS selection
                   WHERE selection.demand_id = OLD.id
               )
        ) THEN
            RAISE EXCEPTION 'Existing membership, offer, selection, or order blocks automatic grouping'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER guard_demand_group_attachment_v1
BEFORE INSERT OR UPDATE OF group_id ON public.demands
FOR EACH ROW
EXECUTE FUNCTION public.guard_demand_group_attachment_v1();

REVOKE ALL ON FUNCTION public.guard_demand_group_attachment_v1()
    FROM PUBLIC, anon, authenticated, service_role;

DO $grouping_guard_acl_contract$
DECLARE
    v_function_oid OID := to_regprocedure('public.guard_demand_group_attachment_v1()');
    v_owner_oid OID;
BEGIN
    SELECT proowner INTO STRICT v_owner_oid FROM pg_proc WHERE oid = v_function_oid;
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS routine
        CROSS JOIN LATERAL aclexplode(
            COALESCE(routine.proacl, acldefault('f', routine.proowner))
        ) AS privilege
        WHERE routine.oid = v_function_oid
          AND (privilege.grantor <> v_owner_oid OR privilege.grantee <> v_owner_oid)
    ) THEN
        RAISE EXCEPTION 'Grouping trigger function ACL must remain owner-only';
    END IF;
END;
$grouping_guard_acl_contract$;

NOTIFY pgrst, 'reload schema';

COMMIT;