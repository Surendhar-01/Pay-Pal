-- DRAFT ONLY: approve the exact HP laptop/Computers product-family alias.
-- Requires demand_group_family_aliases from migration 021 to exist first.

BEGIN;

DO $hp_laptop_alias$
BEGIN
    IF to_regclass('public.demand_group_family_aliases') IS NULL THEN
        RAISE EXCEPTION 'HP laptop alias requires demand_group_family_aliases from migration 021';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.demand_group_family_aliases AS alias
        WHERE alias.product_name_key = 'hp laptop'
          AND alias.category_path_key = 'computers'
          AND (
              alias.product_family_key IS DISTINCT FROM 'laptop'
              OR alias.category_key IS DISTINCT FROM 'computer_laptops'
          )
    ) THEN
        RAISE EXCEPTION 'Existing HP laptop alias conflicts with the reviewed canonical mapping';
    END IF;

    INSERT INTO public.demand_group_family_aliases AS alias (
        product_name_key, category_path_key, product_family_key, category_key,
        approval_reference, approved, approved_at
    ) VALUES (
        'hp laptop',
        'computers',
        'laptop',
        'computer_laptops',
        'hp-laptop-computers-approved',
        TRUE,
        now()
    )
    ON CONFLICT (product_name_key, category_path_key) DO UPDATE
    SET approved = EXCLUDED.approved,
        approved_at = COALESCE(alias.approved_at, EXCLUDED.approved_at)
    WHERE alias.product_family_key = EXCLUDED.product_family_key
      AND alias.category_key = EXCLUDED.category_key;

    IF NOT EXISTS (
        SELECT 1
        FROM public.demand_group_family_aliases AS alias
        WHERE alias.product_name_key = 'hp laptop'
          AND alias.category_path_key = 'computers'
          AND alias.product_family_key = 'laptop'
          AND alias.category_key = 'computer_laptops'
          AND alias.approved IS TRUE
          AND alias.approved_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'HP laptop alias was not approved with the expected canonical mapping';
    END IF;
END;
$hp_laptop_alias$;

NOTIFY pgrst, 'reload schema';

COMMIT;