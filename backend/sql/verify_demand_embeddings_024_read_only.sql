-- READ-ONLY verification of the migration 024 demand-embedding contract.
-- Does not create, change, or delete database objects or rows.
-- Run against the intended database with permission to inspect catalogs and count
-- rows in public.demand_embeddings. PL/pgSQL notices report the exact row count.

BEGIN TRANSACTION READ ONLY;

-- pgvector prerequisites required by migration 024.
WITH vector_extension AS (
    SELECT e.oid AS extension_oid, e.extnamespace AS extension_namespace_oid,
           n.nspname AS extension_schema
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname = 'vector'
), vector_type AS (
    SELECT v.extension_oid, v.extension_namespace_oid, v.extension_schema,
           t.oid AS vector_type_oid
    FROM vector_extension AS v
    LEFT JOIN pg_type AS t
      ON t.typnamespace = v.extension_namespace_oid AND t.typname = 'vector'
)
SELECT
    'vector' AS extension_name,
    v.extension_schema,
    v.vector_type_oid::regtype::text AS vector_type,
    COALESCE(EXISTS (
        SELECT 1 FROM pg_operator AS op
        WHERE op.oprnamespace = v.extension_namespace_oid AND op.oprname = '<=>'
          AND op.oprleft = v.vector_type_oid AND op.oprright = v.vector_type_oid
    ), FALSE) AS has_cosine_operator,
    COALESCE(EXISTS (
        SELECT 1 FROM pg_proc AS f
        WHERE f.pronamespace = v.extension_namespace_oid AND f.proname = 'vector_dims'
          AND f.pronargs = 1 AND f.proargtypes[0] = v.vector_type_oid
    ), FALSE) AS has_vector_dims_function,
    CASE
        WHEN v.extension_oid IS NULL THEN 'MISSING'
        WHEN v.vector_type_oid IS NULL THEN 'INCOMPATIBLE'
        WHEN NOT EXISTS (
            SELECT 1 FROM pg_operator AS op
            WHERE op.oprnamespace = v.extension_namespace_oid AND op.oprname = '<=>'
              AND op.oprleft = v.vector_type_oid AND op.oprright = v.vector_type_oid
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_proc AS f
            WHERE f.pronamespace = v.extension_namespace_oid AND f.proname = 'vector_dims'
              AND f.pronargs = 1 AND f.proargtypes[0] = v.vector_type_oid
        ) THEN 'INCOMPATIBLE'
        ELSE 'MATCH'
    END AS result
FROM (SELECT 1 AS anchor) AS a
LEFT JOIN vector_type AS v ON TRUE;

-- Table columns: expected types, nullability, and defaults from migration 024.
-- A vector dimension match is determined from the actual PostgreSQL typmod.
WITH vector_extension AS (
    SELECT n.nspname AS extension_schema, t.oid AS vector_type_oid
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    JOIN pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
    WHERE e.extname = 'vector'
), expected_columns(column_name, expected_type) AS (
    VALUES
        ('demand_id', 'uuid'),
        ('embedding', 'vector(768)'),
        ('embedding_model', 'text'),
        ('embedding_version', 'text'),
        ('input_hash', 'text'),
        ('updated_at', 'timestamp with time zone')
), observed_columns AS (
    SELECT
        expected.column_name,
        expected.expected_type,
        attribute.attname IS NOT NULL AS column_exists,
        attribute.atttypid,
        attribute.atttypmod,
        format_type(attribute.atttypid, attribute.atttypmod) AS actual_type,
        attribute.attnotnull AS actual_not_null,
        pg_get_expr(default_value.adbin, default_value.adrelid) AS actual_default,
        vector_extension.vector_type_oid
    FROM expected_columns AS expected
    LEFT JOIN pg_class AS relation
      ON relation.oid = to_regclass('public.demand_embeddings')
    LEFT JOIN pg_attribute AS attribute
      ON attribute.attrelid = relation.oid
     AND attribute.attname = expected.column_name
     AND attribute.attnum > 0
     AND NOT attribute.attisdropped
    LEFT JOIN pg_attrdef AS default_value
      ON default_value.adrelid = relation.oid
     AND default_value.adnum = attribute.attnum
    LEFT JOIN vector_extension ON TRUE
)
SELECT
    column_name,
    expected_type,
    actual_type,
    actual_not_null,
    actual_default,
    CASE
        WHEN NOT column_exists THEN FALSE
        WHEN expected_type = 'vector(768)' THEN
            (atttypid = vector_type_oid
             AND format_type(atttypid, atttypmod) ~ '(^|[.])vector\(768\)$')
        WHEN expected_type = 'timestamp with time zone' THEN
            atttypid = 'timestamptz'::regtype
        ELSE format_type(atttypid, atttypmod) = expected_type
    END AS type_matches,
    CASE WHEN NOT column_exists THEN FALSE ELSE actual_not_null END AS not_null_matches,
    CASE
        WHEN NOT column_exists THEN FALSE
        WHEN column_name = 'updated_at' THEN
            regexp_replace(lower(COALESCE(actual_default, '')), '[[:space:]()]', '', 'g') = 'now'
        ELSE actual_default IS NULL
    END AS default_matches,
    CASE
        WHEN NOT column_exists THEN 'MISSING'
        WHEN NOT (
            CASE
                WHEN expected_type = 'vector(768)' THEN
                    atttypid = vector_type_oid
                    AND format_type(atttypid, atttypmod) ~ '(^|[.])vector\(768\)$'
                WHEN expected_type = 'timestamp with time zone' THEN atttypid = 'timestamptz'::regtype
                ELSE format_type(atttypid, atttypmod) = expected_type
            END
        ) OR NOT actual_not_null OR NOT (
            CASE
                WHEN column_name = 'updated_at' THEN
                    regexp_replace(lower(COALESCE(actual_default, '')), '[[:space:]()]', '', 'g') = 'now'
                ELSE actual_default IS NULL
            END
        ) THEN 'INCOMPATIBLE'
        ELSE 'MATCH'
    END AS result
FROM observed_columns
ORDER BY column_name;

-- All constraints on the table, with explicit checks for the exact primary key,
-- three non-empty metadata checks, and absence of an undeclared demand foreign key.
WITH table_info AS (
    SELECT to_regclass('public.demand_embeddings') AS table_oid
), column_attnums AS (
    SELECT a.attname, a.attnum
    FROM table_info AS t
    JOIN pg_attribute AS a ON a.attrelid = t.table_oid
    WHERE a.attnum > 0 AND NOT a.attisdropped
), check_expectations(column_name) AS (
    VALUES ('embedding_model'), ('embedding_version'), ('input_hash')
), check_matches AS (
    SELECT expected.column_name,
           EXISTS (
               SELECT 1
               FROM pg_constraint AS c
               JOIN column_attnums AS a ON a.attnum = c.conkey[1]
               WHERE c.conrelid = (SELECT table_oid FROM table_info)
                 AND c.contype = 'c'
                                 AND c.convalidated
                 AND cardinality(c.conkey) = 1
                 AND a.attname = expected.column_name
                 AND regexp_replace(
                     lower(pg_get_expr(c.conbin, c.conrelid)),
                     '[[:space:]()]|::text', '', 'g'
                 ) = expected.column_name || '<>' || chr(39) || chr(39)
           ) AS matches
    FROM check_expectations AS expected
), constraint_summary AS (
    SELECT
        t.table_oid,
        COALESCE(bool_or(
            c.contype = 'p'
            AND cardinality(c.conkey) = 1
            AND c.conkey[1] = (SELECT attnum FROM column_attnums WHERE attname = 'demand_id')
            AND c.convalidated
        ), FALSE) AS primary_key_matches,
        (SELECT count(*) = 3 AND bool_and(matches) FROM check_matches) AS checks_match,
        count(c.oid) FILTER (WHERE c.contype = 'f') AS foreign_key_count,
        count(c.oid) FILTER (WHERE c.contype = 'p') AS primary_key_count,
        count(c.oid) FILTER (WHERE c.contype = 'c') AS check_count
    FROM table_info AS t
    LEFT JOIN pg_constraint AS c ON c.conrelid = t.table_oid
    GROUP BY t.table_oid
)
SELECT
    c.conname AS constraint_name,
    c.contype AS constraint_type,
    pg_get_constraintdef(c.oid, TRUE) AS definition,
    c.convalidated AS is_validated
FROM table_info AS t
JOIN pg_constraint AS c ON c.conrelid = t.table_oid
ORDER BY c.contype, c.conname;

WITH table_info AS (
    SELECT to_regclass('public.demand_embeddings') AS table_oid
), column_attnums AS (
    SELECT a.attname, a.attnum
    FROM table_info AS t
    JOIN pg_attribute AS a ON a.attrelid = t.table_oid
    WHERE a.attnum > 0 AND NOT a.attisdropped
), check_expectations(column_name) AS (
    VALUES ('embedding_model'), ('embedding_version'), ('input_hash')
), check_matches AS (
    SELECT expected.column_name,
           EXISTS (
               SELECT 1
               FROM pg_constraint AS c
               JOIN column_attnums AS a ON a.attnum = c.conkey[1]
               WHERE c.conrelid = (SELECT table_oid FROM table_info)
                                 AND c.contype = 'c' AND c.convalidated AND cardinality(c.conkey) = 1
                 AND a.attname = expected.column_name
                 AND regexp_replace(
                     lower(pg_get_expr(c.conbin, c.conrelid)),
                     '[[:space:]()]|::text', '', 'g'
                 ) = expected.column_name || '<>' || chr(39) || chr(39)
           ) AS matches
    FROM check_expectations AS expected
), constraint_summary AS (
    SELECT
        t.table_oid,
        COALESCE(bool_or(
            c.contype = 'p' AND cardinality(c.conkey) = 1
            AND c.conkey[1] = (SELECT attnum FROM column_attnums WHERE attname = 'demand_id')
            AND c.convalidated
        ), FALSE) AS primary_key_matches,
        COALESCE((SELECT count(*) = 3 AND bool_and(matches) FROM check_matches), FALSE) AS checks_match,
        count(c.oid) FILTER (WHERE c.contype = 'f') AS foreign_key_count,
        count(c.oid) FILTER (WHERE c.contype = 'p') AS primary_key_count,
        count(c.oid) FILTER (WHERE c.contype = 'c') AS check_count
    FROM table_info AS t
    LEFT JOIN pg_constraint AS c ON c.conrelid = t.table_oid
    GROUP BY t.table_oid
)
SELECT
    COALESCE(primary_key_matches, FALSE) AS demand_id_primary_key_matches,
    COALESCE(checks_match, FALSE) AS all_three_nonempty_checks_match,
    COALESCE(foreign_key_count, 0) AS foreign_key_count_expected_zero,
    COALESCE(primary_key_count, 0) AS primary_key_count_expected_one,
    COALESCE(check_count, 0) AS check_count_expected_three,
    CASE
        WHEN table_oid IS NULL THEN 'MISSING TABLE'
        WHEN NOT primary_key_matches OR NOT checks_match OR foreign_key_count <> 0
          OR primary_key_count <> 1 OR check_count <> 3 THEN 'INCOMPATIBLE'
        ELSE 'MATCH'
    END AS result
FROM constraint_summary;

-- Index definitions and expected model/version index contract.
SELECT
    index_relation.oid::regclass AS index_name,
    table_relation.oid::regclass AS table_name,
    access_method.amname AS access_method,
    index_info.indisunique AS is_unique,
    index_info.indisvalid AS is_valid,
    index_info.indisready AS is_ready,
    index_info.indnkeyatts AS key_count,
    index_info.indnatts AS total_attribute_count,
    pg_get_indexdef(index_info.indexrelid) AS definition,
    CASE WHEN index_info.indexrelid = to_regclass('public.demand_embeddings_model_version_idx')
         THEN pg_get_expr(index_info.indpred, index_info.indrelid) END AS predicate
FROM pg_index AS index_info
JOIN pg_class AS index_relation ON index_relation.oid = index_info.indexrelid
JOIN pg_class AS table_relation ON table_relation.oid = index_info.indrelid
JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_relation.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
WHERE (table_namespace.nspname = 'public' AND table_relation.relname = 'demand_embeddings')
   OR index_info.indexrelid = to_regclass('public.demand_embeddings_model_version_idx')
ORDER BY index_name::text;

WITH target_table AS (
    SELECT to_regclass('public.demand_embeddings') AS table_oid
), expected_index AS (
    SELECT i.*
    FROM pg_index AS i
    WHERE i.indexrelid = to_regclass('public.demand_embeddings_model_version_idx')
)
SELECT
    EXISTS (
        SELECT 1 FROM expected_index AS i
        JOIN pg_class AS table_relation ON table_relation.oid = i.indrelid
        WHERE i.indrelid = (SELECT table_oid FROM target_table)
          AND table_relation.oid = to_regclass('public.demand_embeddings')
          AND i.indisvalid AND i.indisready AND NOT i.indisunique
          AND i.indnkeyatts = 2 AND i.indnatts = 2 AND i.indpred IS NULL
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'embedding_model'
          AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'embedding_version'
          AND (SELECT am.amname FROM pg_class AS ic JOIN pg_am AS am ON am.oid = ic.relam
               WHERE ic.oid = i.indexrelid) = 'btree'
    ) AS expected_index_matches,
    to_regclass('public.demand_embeddings_model_version_idx') IS NOT NULL AS named_index_exists;

-- RLS, policies, and direct table grants. Migration 024 enables RLS but creates
-- no policy; it grants service_role SELECT/INSERT/UPDATE and revokes other roles.
SELECT
    c.relrowsecurity AS row_level_security_enabled,
    c.relforcerowsecurity AS row_level_security_forced,
    (SELECT count(*) FROM pg_policies AS p
     WHERE p.schemaname = 'public' AND p.tablename = 'demand_embeddings') AS policy_count_expected_zero,
    acl.grantee_name,
    acl.privilege_type,
    acl.is_grantable
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN LATERAL (
    SELECT CASE WHEN expanded.grantee = 0 THEN 'PUBLIC'
                ELSE pg_get_userbyid(expanded.grantee) END AS grantee_name,
           expanded.privilege_type,
           expanded.is_grantable
    FROM aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS expanded
) AS acl ON TRUE
WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
ORDER BY acl.grantee_name, acl.privilege_type;

WITH table_info AS (
    SELECT c.oid, c.relowner, c.relacl, c.relrowsecurity
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
), direct_grants AS (
    SELECT CASE WHEN expanded.grantee = 0 THEN 'PUBLIC'
                ELSE pg_get_userbyid(expanded.grantee) END AS grantee_name,
           expanded.privilege_type
    FROM table_info AS t
    CROSS JOIN LATERAL aclexplode(COALESCE(t.relacl, acldefault('r', t.relowner))) AS expanded
)
SELECT
    EXISTS (SELECT 1 FROM table_info WHERE relrowsecurity) AS rls_enabled,
    (SELECT count(*) = 0 FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'demand_embeddings') AS has_no_policies_as_migration_defines,
    (SELECT count(*) = 3 FROM direct_grants
     WHERE grantee_name = 'service_role'
       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE')) AS service_role_has_three_expected_direct_grants,
    NOT EXISTS (SELECT 1 FROM direct_grants
                WHERE grantee_name = 'service_role'
                  AND privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE')) AS service_role_has_no_extra_direct_grants,
    NOT EXISTS (SELECT 1 FROM direct_grants
                WHERE grantee_name IN ('PUBLIC', 'anon', 'authenticated')) AS no_public_anon_or_authenticated_table_grants,
    to_regclass('public.demand_embeddings') IS NOT NULL AS table_exists;

-- Exact row count, safely skipped when the table is absent. This uses dynamic
-- SELECT only; notices are visible in clients that display PostgreSQL NOTICEs.
DO $embedding_row_count_read_only$
DECLARE
    embedding_rows BIGINT;
BEGIN
    IF to_regclass('public.demand_embeddings') IS NULL THEN
        RAISE NOTICE '024 EMBEDDING ROW COUNT: unavailable; public.demand_embeddings is absent.';
    ELSE
        EXECUTE 'SELECT count(*) FROM public.demand_embeddings' INTO embedding_rows;
        RAISE NOTICE '024 EMBEDDING ROW COUNT: %', embedding_rows;
    END IF;
END;
$embedding_row_count_read_only$;

-- Show every same-name public function, including overloads or incompatible
-- signatures, plus the exact stored definition and ACL.
SELECT
    p.oid::regprocedure::text AS actual_signature,
    p.proargnames AS argument_and_output_names,
    pg_get_function_result(p.oid) AS actual_return_type,
    language.lanname AS language,
    p.provolatile AS volatility_code,
    p.prosecdef AS security_definer,
    p.proconfig AS function_settings,
    pg_get_functiondef(p.oid) AS exact_function_definition,
    (SELECT string_agg(
                CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                     ELSE pg_get_userbyid(acl.grantee) END
                || ':' || acl.privilege_type
                || CASE WHEN acl.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END,
                ', ' ORDER BY acl.grantee, acl.privilege_type
            )
     FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) AS acl
    ) AS direct_acl
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
JOIN pg_language AS language ON language.oid = p.prolang
WHERE n.nspname = 'public'
  AND p.proname = 'search_demand_embedding_candidates_v1'
ORDER BY p.oid::regprocedure::text;

-- Function argument, output, body, security, and service-role ACL contract.
WITH vector_type AS (
    SELECT t.oid AS vector_type_oid, n.nspname AS vector_schema
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    JOIN pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
    WHERE e.extname = 'vector'
), named_functions AS (
    SELECT p.*, n.nspname AS function_schema, language.lanname,
           pg_get_function_result(p.oid) AS result_definition,
           regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g') AS compact_source,
           vector_type.vector_type_oid, vector_type.vector_schema
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    JOIN pg_language AS language ON language.oid = p.prolang
    LEFT JOIN vector_type ON TRUE
    WHERE n.nspname = 'public'
      AND p.proname = 'search_demand_embedding_candidates_v1'
), exact_function AS (
    SELECT f.*
    FROM named_functions AS f
    WHERE f.pronargs = 6
      AND f.proargtypes[0] = f.vector_type_oid
      AND f.proargtypes[1] = 'uuid'::regtype
      AND f.proargtypes[2] = 'integer'::regtype
      AND f.proargtypes[3] = 'double precision'::regtype
      AND f.proargtypes[4] = 'text'::regtype
      AND f.proargtypes[5] = 'text'::regtype
), function_contract AS (
    SELECT
        f.*,
        f.proargmodes::text = '{i,i,i,i,i,i,t,t}' AS modes_match,
        f.proargnames = ARRAY[
            'p_query_embedding', 'p_exclude_demand_id', 'p_candidate_limit',
            'p_max_cosine_distance', 'p_embedding_model', 'p_embedding_version',
            'demand_id', 'cosine_distance'
        ]::text[] AS names_match,
        f.proallargtypes IS NOT NULL
          AND f.proallargtypes[7] = 'uuid'::regtype
          AND f.proallargtypes[8] = 'double precision'::regtype AS output_types_match,
        f.prorettype = 'record'::regtype AND f.proretset AS returns_table_record,
        f.lanname = 'sql' AS language_matches,
        f.provolatile = 's' AS stable_function,
        NOT f.prosecdef AS security_invoker,
        COALESCE(f.proconfig @> ARRAY['search_path=pg_catalog, public'], FALSE) AS search_path_matches,
        position('p_query_embeddingisnotnull' IN f.compact_source) > 0
          AND position('vector_dims(p_query_embedding)=768' IN f.compact_source) > 0
          AND position('p_candidate_limitbetween1and100' IN f.compact_source) > 0
          AND position('p_max_cosine_distancebetween0and2' IN f.compact_source) > 0
          AND position('p_embedding_modelisnotnull' IN f.compact_source) > 0
          AND position('p_embedding_versionisnotnull' IN f.compact_source) > 0
          AND position('stored.embedding_model=p_embedding_model' IN f.compact_source) > 0
          AND position('stored.embedding_version=p_embedding_version' IN f.compact_source) > 0
          AND position('stored.demand_idisdistinctfromp_exclude_demand_id' IN f.compact_source) > 0
          AND position('orderby' IN f.compact_source) > 0
          AND position('stored.demand_id' IN substring(f.compact_source FROM position('orderby' IN f.compact_source))) > 0
          AND position('limitcasewhenp_candidate_limitbetween1and100thenp_candidate_limitelse0end' IN f.compact_source) > 0
          AND position('<=>' IN f.compact_source) > 0 AS required_search_logic_matches
    FROM exact_function AS f
), function_acl AS (
    SELECT
        f.oid,
        has_function_privilege('service_role', f.oid, 'EXECUTE') AS service_role_can_execute,
        has_function_privilege('anon', f.oid, 'EXECUTE') AS anon_can_execute,
        has_function_privilege('authenticated', f.oid, 'EXECUTE') AS authenticated_can_execute,
        NOT EXISTS (
            SELECT 1
            FROM aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS a
            WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'
        ) AS public_execute_revoked,
        NOT EXISTS (
            SELECT 1
            FROM aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname IN ('anon', 'authenticated')
              AND a.privilege_type = 'EXECUTE'
        ) AS anon_authenticated_execute_revoked,
        EXISTS (
            SELECT 1
            FROM aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname = 'service_role' AND a.privilege_type = 'EXECUTE'
        ) AS service_role_direct_execute_grant
    FROM exact_function AS f
)
SELECT
    (SELECT count(*) FROM named_functions) AS same_name_function_count,
    (SELECT count(*) FROM exact_function) AS exact_signature_count,
    (SELECT modes_match FROM function_contract LIMIT 1) AS argument_modes_match,
    (SELECT names_match FROM function_contract LIMIT 1) AS argument_names_match,
    (SELECT output_types_match FROM function_contract LIMIT 1) AS return_column_types_match,
    (SELECT returns_table_record FROM function_contract LIMIT 1) AS setof_table_return_matches,
    (SELECT result_definition = 'TABLE(demand_id uuid, cosine_distance double precision)'
       FROM function_contract LIMIT 1) AS deparsed_return_type_matches,
    (SELECT language_matches FROM function_contract LIMIT 1) AS language_is_sql,
    (SELECT stable_function FROM function_contract LIMIT 1) AS volatility_is_stable,
    (SELECT security_invoker FROM function_contract LIMIT 1) AS security_is_invoker,
    (SELECT search_path_matches FROM function_contract LIMIT 1) AS search_path_matches,
    (SELECT required_search_logic_matches FROM function_contract LIMIT 1) AS required_search_logic_matches,
    (SELECT service_role_can_execute FROM function_acl LIMIT 1) AS service_role_can_execute,
    (SELECT service_role_direct_execute_grant FROM function_acl LIMIT 1) AS service_role_direct_grant,
    (SELECT public_execute_revoked FROM function_acl LIMIT 1) AS public_execute_revoked,
    (SELECT anon_authenticated_execute_revoked FROM function_acl LIMIT 1) AS anon_authenticated_execute_revoked;

-- Final assessment. Missing prerequisite vector support is reported explicitly;
-- an empty database state is NOT APPLIED, not misreported as partial application.
WITH vector_check AS (
    SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS extension_exists,
           EXISTS (
               SELECT 1
               FROM pg_extension AS e
               JOIN pg_namespace AS n ON n.oid = e.extnamespace
               JOIN pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
               WHERE e.extname = 'vector'
           ) AS vector_type_exists,
           EXISTS (
               SELECT 1
               FROM pg_extension AS e
               JOIN pg_namespace AS n ON n.oid = e.extnamespace
               JOIN pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
               JOIN pg_operator AS op ON op.oprnamespace = n.oid
                 AND op.oprname = '<=>' AND op.oprleft = t.oid AND op.oprright = t.oid
               WHERE e.extname = 'vector'
           ) AS cosine_operator_exists,
           EXISTS (
               SELECT 1
               FROM pg_extension AS e
               JOIN pg_namespace AS n ON n.oid = e.extnamespace
               JOIN pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
               JOIN pg_proc AS f ON f.pronamespace = n.oid AND f.proname = 'vector_dims'
                 AND f.pronargs = 1 AND f.proargtypes[0] = t.oid
               WHERE e.extname = 'vector'
           ) AS vector_dims_exists
), target_table AS (
    SELECT c.oid, c.relkind, c.relrowsecurity
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
), expected_columns(column_name, expected_type) AS (
    VALUES ('demand_id', 'uuid'), ('embedding', 'vector(768)'),
           ('embedding_model', 'text'), ('embedding_version', 'text'),
           ('input_hash', 'text'), ('updated_at', 'timestamp with time zone')
), column_states AS (
    SELECT expected.column_name,
           a.attname IS NOT NULL AS exists,
           CASE
               WHEN a.attname IS NULL THEN FALSE
               WHEN expected.expected_type = 'vector(768)' THEN
                   a.atttypid = (SELECT t.oid FROM pg_extension AS e
                                 JOIN pg_type AS t ON t.typnamespace = e.extnamespace
                                 WHERE e.extname = 'vector' AND t.typname = 'vector')
                   AND format_type(a.atttypid, a.atttypmod) ~ '(^|[.])vector\(768\)$'
               WHEN expected.expected_type = 'timestamp with time zone' THEN a.atttypid = 'timestamptz'::regtype
               ELSE format_type(a.atttypid, a.atttypmod) = expected.expected_type
           END AS type_ok,
           COALESCE(a.attnotnull, FALSE) AS not_null_ok,
           CASE
               WHEN a.attname IS NULL THEN FALSE
               WHEN expected.column_name = 'updated_at' THEN
                   regexp_replace(lower(COALESCE(pg_get_expr(d.adbin, d.adrelid), '')),
                                  '[[:space:]()]', '', 'g') = 'now'
               ELSE d.adbin IS NULL
           END AS default_ok
    FROM expected_columns AS expected
    LEFT JOIN target_table AS t ON TRUE
    LEFT JOIN pg_attribute AS a
      ON a.attrelid = t.oid AND a.attname = expected.column_name
     AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_attrdef AS d ON d.adrelid = t.oid AND d.adnum = a.attnum
), table_check AS (
    SELECT
        EXISTS (SELECT 1 FROM target_table) AS table_exists,
        COALESCE(bool_and(exists AND type_ok AND not_null_ok AND default_ok), FALSE) AS columns_match,
        COALESCE(bool_or(exists AND (NOT type_ok OR NOT not_null_ok OR NOT default_ok)), FALSE) AS bad_column,
        COALESCE(bool_or(NOT exists), TRUE) AS missing_column,
        COALESCE((SELECT relkind IN ('r', 'p') FROM target_table LIMIT 1), FALSE) AS relation_kind_matches,
        COALESCE((SELECT relrowsecurity FROM target_table LIMIT 1), FALSE) AS rls_enabled,
        EXISTS (
            SELECT 1 FROM pg_constraint AS c
            WHERE c.conrelid = (SELECT oid FROM target_table LIMIT 1)
              AND c.contype = 'p' AND c.convalidated
              AND cardinality(c.conkey) = 1
              AND c.conkey[1] = (SELECT attnum FROM pg_attribute
                                 WHERE attrelid = (SELECT oid FROM target_table LIMIT 1)
                                   AND attname = 'demand_id' AND NOT attisdropped)
        ) AS primary_key_matches,
        (SELECT count(*) = 3 FROM (VALUES ('embedding_model'), ('embedding_version'), ('input_hash')) AS expected_column(name)
         WHERE EXISTS (
             SELECT 1 FROM pg_constraint AS c
             JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
             WHERE c.conrelid = (SELECT oid FROM target_table LIMIT 1)
                             AND c.contype = 'c' AND c.convalidated AND cardinality(c.conkey) = 1
               AND a.attname = expected_column.name
               AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                  '[[:space:]()]|::text', '', 'g')
                   = expected_column.name || '<>' || chr(39) || chr(39)
         )) AS three_nonempty_checks_match,
                EXISTS (
                        SELECT 1 FROM pg_constraint AS c
                        JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                        WHERE c.conrelid = (SELECT oid FROM target_table LIMIT 1)
                            AND c.contype = 'c'
                            AND a.attname IN ('embedding_model', 'embedding_version', 'input_hash')
                            AND (NOT c.convalidated OR regexp_replace(
                                    lower(pg_get_expr(c.conbin, c.conrelid)),
                                    '[[:space:]()]|::text', '', 'g'
                            ) <> a.attname || '<>' || chr(39) || chr(39))
                ) AS bad_check_definition,
                (SELECT count(*) FROM pg_constraint AS c
                 WHERE c.conrelid = (SELECT oid FROM target_table LIMIT 1)) AS constraint_count,
        NOT EXISTS (SELECT 1 FROM pg_constraint AS c
                    WHERE c.conrelid = (SELECT oid FROM target_table LIMIT 1)
                      AND c.contype = 'f') AS no_foreign_keys,
        EXISTS (
            SELECT 1 FROM pg_index AS i
            JOIN pg_class AS ic ON ic.oid = i.indexrelid
            JOIN pg_am AS am ON am.oid = ic.relam
            WHERE i.indexrelid = to_regclass('public.demand_embeddings_model_version_idx')
              AND i.indrelid = (SELECT oid FROM target_table LIMIT 1)
              AND i.indisvalid AND i.indisready AND NOT i.indisunique
              AND i.indnkeyatts = 2 AND i.indnatts = 2 AND i.indpred IS NULL
              AND am.amname = 'btree'
              AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'embedding_model'
              AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'embedding_version'
        ) AS expected_index_matches,
        (SELECT count(*) = 0 FROM pg_policies
         WHERE schemaname = 'public' AND tablename = 'demand_embeddings') AS no_policies
    FROM column_states
), function_rows AS (
    SELECT p.*, n.nspname AS schema_name, lang.lanname,
           pg_get_function_result(p.oid) AS result_definition,
           regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g') AS compact_source,
           vector_type.oid AS vector_type_oid, vector_namespace.nspname AS vector_schema
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    JOIN pg_language AS lang ON lang.oid = p.prolang
    LEFT JOIN pg_extension AS e ON e.extname = 'vector'
    LEFT JOIN pg_namespace AS vector_namespace ON vector_namespace.oid = e.extnamespace
    LEFT JOIN pg_type AS vector_type
      ON vector_type.typnamespace = vector_namespace.oid AND vector_type.typname = 'vector'
    WHERE n.nspname = 'public' AND p.proname = 'search_demand_embedding_candidates_v1'
), exact_function AS (
    SELECT f.*
    FROM function_rows AS f
    WHERE f.pronargs = 6
      AND f.proargtypes[0] = f.vector_type_oid
      AND f.proargtypes[1] = 'uuid'::regtype
      AND f.proargtypes[2] = 'integer'::regtype
      AND f.proargtypes[3] = 'double precision'::regtype
      AND f.proargtypes[4] = 'text'::regtype
      AND f.proargtypes[5] = 'text'::regtype
), function_check AS (
    SELECT
        (SELECT count(*) FROM function_rows) AS same_name_count,
        (SELECT count(*) FROM exact_function) AS exact_signature_count,
        EXISTS (
            SELECT 1 FROM exact_function AS f
            WHERE f.proargmodes::text = '{i,i,i,i,i,i,t,t}'
              AND f.proargnames = ARRAY[
                  'p_query_embedding', 'p_exclude_demand_id', 'p_candidate_limit',
                  'p_max_cosine_distance', 'p_embedding_model', 'p_embedding_version',
                  'demand_id', 'cosine_distance'
              ]::text[]
              AND f.proallargtypes[7] = 'uuid'::regtype
              AND f.proallargtypes[8] = 'double precision'::regtype
              AND f.prorettype = 'record'::regtype AND f.proretset
              AND f.result_definition = 'TABLE(demand_id uuid, cosine_distance double precision)'
              AND f.lanname = 'sql' AND f.provolatile = 's' AND NOT f.prosecdef
              AND f.proconfig @> ARRAY['search_path=pg_catalog, public']
              AND position('p_query_embeddingisnotnull' IN f.compact_source) > 0
              AND position('vector_dims(p_query_embedding)=768' IN f.compact_source) > 0
              AND position('p_candidate_limitbetween1and100' IN f.compact_source) > 0
              AND position('p_max_cosine_distancebetween0and2' IN f.compact_source) > 0
              AND position('p_embedding_modelisnotnull' IN f.compact_source) > 0
              AND position('btrim(p_embedding_model)<>' || chr(39) || chr(39) IN f.compact_source) > 0
              AND position('p_embedding_versionisnotnull' IN f.compact_source) > 0
              AND position('btrim(p_embedding_version)<>' || chr(39) || chr(39) IN f.compact_source) > 0
              AND position('stored.embedding_model=p_embedding_model' IN f.compact_source) > 0
              AND position('stored.embedding_version=p_embedding_version' IN f.compact_source) > 0
              AND position('stored.demand_idisdistinctfromp_exclude_demand_id' IN f.compact_source) > 0
              AND position('stored.demand_id' IN f.compact_source) > 0
              AND position('<=>' IN f.compact_source) > 0
              AND position('<=p_max_cosine_distance' IN f.compact_source) > 0
              AND position('orderby' IN f.compact_source) > 0
              AND position(
                  'orderbystored.embeddingoperator(' || lower(f.vector_schema)
                  || '.<=>)p_query_embedding,stored.demand_id'
                  IN f.compact_source
              ) > 0
              AND position('limitcasewhenp_candidate_limitbetween1and100thenp_candidate_limitelse0end' IN f.compact_source) > 0
        ) AS definition_matches
), function_acl AS (
    SELECT
        EXISTS (
            SELECT 1 FROM exact_function AS f
            WHERE has_function_privilege('service_role', f.oid, 'EXECUTE')
        ) AS service_role_can_execute,
        EXISTS (
            SELECT 1 FROM exact_function AS f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname = 'service_role' AND a.privilege_type = 'EXECUTE'
        ) AS service_role_direct_grant,
        NOT EXISTS (
            SELECT 1 FROM exact_function AS f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS a
            WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'
        ) AS public_execute_revoked,
        NOT EXISTS (
            SELECT 1 FROM exact_function AS f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname IN ('anon', 'authenticated') AND a.privilege_type = 'EXECUTE'
        ) AS anon_authenticated_execute_revoked
), table_acl AS (
    SELECT
        EXISTS (
            SELECT 1 FROM target_table AS t
            CROSS JOIN LATERAL aclexplode(COALESCE(
                (SELECT c.relacl FROM pg_class AS c WHERE c.oid = t.oid),
                acldefault('r', (SELECT c.relowner FROM pg_class AS c WHERE c.oid = t.oid))
            )) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname = 'service_role' AND a.privilege_type = 'SELECT'
        )
        AND EXISTS (
            SELECT 1 FROM target_table AS t
            CROSS JOIN LATERAL aclexplode(COALESCE(
                (SELECT c.relacl FROM pg_class AS c WHERE c.oid = t.oid),
                acldefault('r', (SELECT c.relowner FROM pg_class AS c WHERE c.oid = t.oid))
            )) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname = 'service_role' AND a.privilege_type = 'INSERT'
        )
        AND EXISTS (
            SELECT 1 FROM target_table AS t
            CROSS JOIN LATERAL aclexplode(COALESCE(
                (SELECT c.relacl FROM pg_class AS c WHERE c.oid = t.oid),
                acldefault('r', (SELECT c.relowner FROM pg_class AS c WHERE c.oid = t.oid))
            )) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname = 'service_role' AND a.privilege_type = 'UPDATE'
        ) AS service_role_grants_match,
        NOT EXISTS (
            SELECT 1 FROM target_table AS t
            CROSS JOIN LATERAL aclexplode(COALESCE(
                (SELECT c.relacl FROM pg_class AS c WHERE c.oid = t.oid),
                acldefault('r', (SELECT c.relowner FROM pg_class AS c WHERE c.oid = t.oid))
            )) AS a
            LEFT JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE (a.grantee = 0 OR r.rolname IN ('anon', 'authenticated'))
        ) AS public_client_grants_revoked
        ,NOT EXISTS (
            SELECT 1 FROM target_table AS t
            CROSS JOIN LATERAL aclexplode(COALESCE(
                (SELECT c.relacl FROM pg_class AS c WHERE c.oid = t.oid),
                acldefault('r', (SELECT c.relowner FROM pg_class AS c WHERE c.oid = t.oid))
            )) AS a
            JOIN pg_roles AS r ON r.oid = a.grantee
            WHERE r.rolname = 'service_role'
              AND a.privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE')
        ) AS no_extra_service_role_grants
), object_presence AS (
    SELECT
        EXISTS (SELECT 1 FROM target_table) AS table_exists,
        (SELECT count(*) FROM function_rows) AS same_name_function_count,
        to_regclass('public.demand_embeddings_model_version_idx') IS NOT NULL AS named_index_exists
), assessment AS (
    SELECT
        v.extension_exists AND v.vector_type_exists AND v.cosine_operator_exists AND v.vector_dims_exists
            AS vector_prerequisite_matches,
        o.table_exists,
        o.same_name_function_count,
        o.named_index_exists,
        tc.columns_match,
        tc.bad_column,
        tc.missing_column,
        tc.relation_kind_matches,
        tc.rls_enabled,
        tc.primary_key_matches,
        tc.three_nonempty_checks_match,
        tc.bad_check_definition,
        tc.constraint_count,
        tc.no_foreign_keys,
        tc.expected_index_matches,
        tc.no_policies,
        fc.exact_signature_count,
        fc.definition_matches,
        fa.service_role_can_execute,
        fa.service_role_direct_grant,
        fa.public_execute_revoked,
        fa.anon_authenticated_execute_revoked,
        ta.service_role_grants_match,
        ta.public_client_grants_revoked,
        ta.no_extra_service_role_grants,
        (o.table_exists OR o.same_name_function_count > 0 OR o.named_index_exists) AS any_024_artifact,
        (tc.bad_column
         OR (o.table_exists AND (tc.missing_column
             OR NOT tc.relation_kind_matches
             OR NOT tc.primary_key_matches AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM target_table) AND contype = 'p')
             OR tc.bad_check_definition
             OR tc.constraint_count > 4
             OR NOT tc.no_foreign_keys
             OR NOT tc.no_policies
             OR NOT ta.public_client_grants_revoked
             OR NOT ta.no_extra_service_role_grants))
         OR (o.named_index_exists AND NOT tc.expected_index_matches)
         OR (o.same_name_function_count > 0 AND (o.same_name_function_count <> 1
             OR fc.exact_signature_count <> 1 OR NOT fc.definition_matches
             OR NOT fa.public_execute_revoked OR NOT fa.anon_authenticated_execute_revoked))) AS incompatible_artifact,
        (tc.columns_match AND tc.relation_kind_matches AND tc.rls_enabled AND tc.primary_key_matches
         AND tc.three_nonempty_checks_match AND tc.no_foreign_keys AND tc.expected_index_matches
         AND tc.no_policies AND ta.service_role_grants_match AND ta.public_client_grants_revoked
         AND ta.no_extra_service_role_grants
         AND o.same_name_function_count = 1 AND fc.exact_signature_count = 1
         AND fc.definition_matches AND fa.service_role_can_execute AND fa.service_role_direct_grant
         AND fa.public_execute_revoked AND fa.anon_authenticated_execute_revoked
         AND v.extension_exists AND v.vector_type_exists AND v.cosine_operator_exists AND v.vector_dims_exists)
            AS all_contracts_match
    FROM vector_check AS v
    CROSS JOIN object_presence AS o
    CROSS JOIN table_check AS tc
    CROSS JOIN function_check AS fc
    CROSS JOIN function_acl AS fa
    CROSS JOIN table_acl AS ta
)
SELECT
    CASE
        WHEN all_contracts_match THEN 'ALREADY FULLY APPLIED'
        WHEN incompatible_artifact THEN 'INCOMPATIBLE - MANUAL REVIEW REQUIRED'
        WHEN NOT any_024_artifact AND NOT vector_prerequisite_matches
            THEN 'NOT APPLIED - VECTOR PREREQUISITE MISSING'
        WHEN NOT any_024_artifact THEN 'NOT APPLIED'
        ELSE 'PARTIALLY APPLIED - SAFE REPAIR REQUIRED'
    END AS migration_024_status,
    vector_prerequisite_matches,
    table_exists,
    same_name_function_count,
    named_index_exists,
    columns_match,
    relation_kind_matches,
    rls_enabled,
    primary_key_matches,
    three_nonempty_checks_match,
    bad_check_definition,
    constraint_count,
    no_foreign_keys,
    expected_index_matches,
    no_policies,
    exact_signature_count,
    definition_matches,
    service_role_grants_match,
    public_client_grants_revoked,
    no_extra_service_role_grants,
    service_role_can_execute,
    service_role_direct_grant,
    public_execute_revoked,
    anon_authenticated_execute_revoked
FROM assessment;

COMMIT;