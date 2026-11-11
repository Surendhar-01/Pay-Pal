-- Persist catalogue match classification and preserve only verified PayPal
-- purchases as customer-visible orders through the backend API.
BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.orders') IS NULL
       OR to_regclass('public.payments') IS NULL
       OR to_regclass('public.payment_events') IS NULL THEN
        RAISE EXCEPTION 'Paid-order visibility requires orders, payments, and payment_events';
    END IF;
END;
$preflight$;

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS match_classification TEXT;

DO $classification_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.orders'::regclass
          AND conname = 'orders_match_classification_valid'
    ) THEN
        ALTER TABLE public.orders
            ADD CONSTRAINT orders_match_classification_valid
            CHECK (
                match_classification IS NULL
                OR match_classification IN ('EXACT_MATCH', 'CLOSEST_ALTERNATIVE')
            )
            NOT VALID;
        ALTER TABLE public.orders
            VALIDATE CONSTRAINT orders_match_classification_valid;
    END IF;
END;
$classification_constraint$;

DO $catalogue_classification_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.orders'::regclass
          AND conname = 'orders_catalogue_match_classification_required'
    ) THEN
        ALTER TABLE public.orders
            ADD CONSTRAINT orders_catalogue_match_classification_required
            CHECK (
                accepted_terms->>'type' IS DISTINCT FROM 'catalogue_purchase'
                OR (
                    match_classification IS NOT NULL
                    AND match_classification IN ('EXACT_MATCH', 'CLOSEST_ALTERNATIVE')
                )
            )
            NOT VALID;
    END IF;
END;
$catalogue_classification_constraint$;

DROP FUNCTION IF EXISTS public.create_catalogue_order_v1(UUID, UUID, TEXT, JSONB, INTEGER, NUMERIC, TEXT);

CREATE FUNCTION public.create_catalogue_order_v1(
    p_customer_id UUID,
    p_idempotency_key UUID,
    p_product_id TEXT,
    p_product_snapshot JSONB,
    p_quantity INTEGER,
    p_unit_price NUMERIC,
    p_currency TEXT,
    p_match_classification TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_order public.orders%ROWTYPE;
    v_total NUMERIC(12,2);
BEGIN
    IF auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'Service role required' USING ERRCODE = '42501';
    END IF;
    IF p_customer_id IS NULL
       OR p_idempotency_key IS NULL
       OR p_product_id IS NULL OR btrim(p_product_id) = ''
       OR p_product_snapshot IS NULL OR jsonb_typeof(p_product_snapshot) <> 'object'
       OR p_product_snapshot->>'product_id' IS DISTINCT FROM p_product_id
       OR p_product_snapshot->>'currency' IS DISTINCT FROM p_currency
       OR p_product_snapshot->>'effective_price' IS NULL
       OR (p_product_snapshot->>'effective_price')::NUMERIC IS DISTINCT FROM round(p_unit_price, 2)
       OR p_quantity IS NULL OR p_quantity < 1
       OR p_unit_price IS NULL OR p_unit_price <= 0
       OR p_currency IS NULL OR p_currency !~ '^[A-Z]{3}$'
       OR p_match_classification IS NULL
       OR p_match_classification NOT IN ('EXACT_MATCH', 'CLOSEST_ALTERNATIVE') THEN
        RAISE EXCEPTION 'Catalogue order details are invalid' USING ERRCODE = '22023';
    END IF;
    v_total := round(p_unit_price * p_quantity, 2);
    IF v_total <= 0 OR v_total > 9999999999.99 THEN
        RAISE EXCEPTION 'Catalogue order total is invalid' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_idempotency_key::TEXT, 0));
    SELECT * INTO v_order
    FROM public.orders
    WHERE checkout_idempotency_key = p_idempotency_key
    FOR UPDATE;
    IF FOUND THEN
        IF v_order.match_classification IS NULL THEN
            RAISE EXCEPTION 'CHECKOUT_LEGACY_ORDER_CLASSIFICATION_MISSING'
                USING ERRCODE = 'P0001';
        END IF;
        IF v_order.customer_id IS DISTINCT FROM p_customer_id
           OR v_order.product_id IS DISTINCT FROM p_product_id
           OR v_order.quantity IS DISTINCT FROM p_quantity
           OR v_order.unit_price IS DISTINCT FROM round(p_unit_price, 2)
           OR v_order.total_price IS DISTINCT FROM v_total
           OR upper(btrim(v_order.accepted_terms->>'currency')) IS DISTINCT FROM p_currency
           OR v_order.match_classification IS DISTINCT FROM p_match_classification THEN
            RAISE EXCEPTION 'CHECKOUT_IDEMPOTENCY_CONFLICT' USING ERRCODE = 'P0001';
        END IF;
        RETURN to_jsonb(v_order) || jsonb_build_object(
            'total_amount', v_order.total_price,
            'currency', p_currency,
            'order_reference', v_order.order_number
        );
    END IF;

    INSERT INTO public.orders (
        order_number, customer_id, product_name, quantity, total_price,
        status, payment_status, order_status, product_id, product_snapshot,
        unit_price, checkout_idempotency_key, accepted_terms, match_classification
    ) VALUES (
        'CAT-' || upper(replace(gen_random_uuid()::TEXT, '-', '')),
        p_customer_id,
        COALESCE(NULLIF(p_product_snapshot->>'name', ''), p_product_id),
        p_quantity,
        v_total,
        'Approved',
        'Not Initiated',
        'Pending Payment',
        p_product_id,
        p_product_snapshot,
        round(p_unit_price, 2),
        p_idempotency_key,
        jsonb_build_object(
            'type', 'catalogue_purchase',
            'currency', p_currency,
            'unit_price', round(p_unit_price, 2),
            'quantity', p_quantity,
            'total_price', v_total,
            'product', p_product_snapshot,
            'match_classification', p_match_classification
        ),
        p_match_classification
    ) RETURNING * INTO v_order;

    RETURN to_jsonb(v_order) || jsonb_build_object(
        'total_amount', v_order.total_price,
        'currency', p_currency,
        'order_reference', v_order.order_number
    );
END;
$function$;

REVOKE ALL ON FUNCTION public.create_catalogue_order_v1(UUID, UUID, TEXT, JSONB, INTEGER, NUMERIC, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_catalogue_order_v1(UUID, UUID, TEXT, JSONB, INTEGER, NUMERIC, TEXT, TEXT)
    TO service_role;

NOTIFY pgrst, 'reload schema';
COMMIT;
