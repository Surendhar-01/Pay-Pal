-- DRAFT ONLY: approve one exact headphone title/category pair for grouping.
-- Requires demand_group_family_aliases from migration 021 to exist first.

BEGIN;

DO $wireless_headphone_alias$
BEGIN
    IF to_regclass('public.demand_group_family_aliases') IS NULL THEN
        RAISE EXCEPTION 'Wireless headphone alias requires demand_group_family_aliases from migration 021';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.demand_group_family_aliases AS alias
        WHERE alias.product_name_key = 'wireless over ear headphones'
          AND alias.category_path_key = 'electronics audio wireless headphones'
          AND (
              alias.product_family_key IS DISTINCT FROM 'wireless_headphones'
              OR alias.category_key IS DISTINCT FROM 'wireless_headphones'
              OR alias.approved IS DISTINCT FROM TRUE
          )
    ) THEN
        RAISE EXCEPTION 'Existing wireless headphone alias conflicts with the reviewed canonical mapping';
    END IF;

    INSERT INTO public.demand_group_family_aliases (
        product_name_key, category_path_key, product_family_key, category_key,
        approval_reference, approved, approved_at
    ) VALUES (
        'wireless over ear headphones',
        'electronics audio wireless headphones',
        'wireless_headphones',
        'wireless_headphones',
        'phase4-compatible-pair',
        TRUE,
        now()
    )
    ON CONFLICT (product_name_key, category_path_key) DO NOTHING;
END;
$wireless_headphone_alias$;

NOTIFY pgrst, 'reload schema';

COMMIT;