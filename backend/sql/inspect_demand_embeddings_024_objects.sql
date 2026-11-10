-- Read-only inspection for reconciling migration 024.
-- Run in the target database with a role that can inspect catalogs and read
-- public.demand_embeddings. Counts follow that role's RLS visibility; use an
-- authorized role that can see all rows for a global count. No vector values
-- are returned.
-- Result sets: columns, constraints, indexes, table privileges, RPCs,
-- embedding data summary, and dependency inventory.

BEGIN TRANSACTION READ ONLY;

-- 1. Table columns, including type modifiers such as vector(768).
WITH target AS (
    SELECT c.oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
)
SELECT
    t.oid IS NOT NULL AS table_exists,
    a.attname AS column_name,
    a.attnum AS ordinal_position,
    format_type(a.atttypid, a.atttypmod) AS data_type,
    NOT a.attnotnull AS is_nullable,
    pg_get_expr(d.adbin, d.adrelid) AS default_expression,
    a.attidentity AS identity_code,
    CASE a.attidentity
        WHEN 'a' THEN 'ALWAYS'
        WHEN 'd' THEN 'BY DEFAULT'
        ELSE NULL
    END AS identity_generation,
    a.attgenerated AS generated_code,
    CASE a.attgenerated
        WHEN 's' THEN 'STORED'
        WHEN 'v' THEN 'VIRTUAL'
        ELSE NULL
    END AS generated_kind
FROM (SELECT 1 AS anchor) AS anchor
LEFT JOIN target AS t ON TRUE
LEFT JOIN pg_attribute AS a
  ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
LEFT JOIN pg_attrdef AS d
  ON d.adrelid = a.attrelid AND d.adnum = a.attnum
ORDER BY a.attnum NULLS LAST;

-- 2. All table constraints, including ordered FK columns and actions.
WITH target AS (
    SELECT c.oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
)
SELECT
    t.oid IS NOT NULL AS table_exists,
    c.conname AS constraint_name,
    c.contype AS constraint_type_code,
    CASE c.contype
        WHEN 'c' THEN 'CHECK'
        WHEN 'f' THEN 'FOREIGN KEY'
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'u' THEN 'UNIQUE'
        WHEN 'x' THEN 'EXCLUSION'
        ELSE c.contype::text
    END AS constraint_type,
    pg_get_constraintdef(c.oid, TRUE) AS definition,
    c.convalidated AS is_validated,
    c.condeferrable AS is_deferrable,
    c.condeferred AS initially_deferred,
    CASE WHEN c.contype = 'f' THEN c.confrelid::regclass::text END AS referenced_table,
    CASE WHEN c.contype = 'f' THEN (
        SELECT array_agg(a.attname ORDER BY key_column.ordinality)
        FROM unnest(c.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
        JOIN pg_attribute AS a
          ON a.attrelid = c.conrelid AND a.attnum = key_column.attnum
    ) END AS referencing_columns,
    CASE WHEN c.contype = 'f' THEN (
        SELECT array_agg(a.attname ORDER BY key_column.ordinality)
        FROM unnest(c.confkey) WITH ORDINALITY AS key_column(attnum, ordinality)
        JOIN pg_attribute AS a
          ON a.attrelid = c.confrelid AND a.attnum = key_column.attnum
    ) END AS referenced_columns,
    CASE c.confdeltype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
        ELSE NULL
    END AS on_delete,
    CASE c.confupdtype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
        ELSE NULL
    END AS on_update
FROM (SELECT 1 AS anchor) AS anchor
LEFT JOIN target AS t ON TRUE
LEFT JOIN pg_constraint AS c ON c.conrelid = t.oid
ORDER BY c.contype, c.conname;

-- 3. Complete index definitions, key expressions, included columns, and flags.
WITH target AS (
    SELECT c.oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
)
SELECT
    t.oid IS NOT NULL AS table_exists,
    index_relation.oid::regclass::text AS index_name,
    index_info.indisunique AS is_unique,
    index_info.indisprimary AS is_primary,
    index_info.indisvalid AS is_valid,
    index_info.indisready AS is_ready,
    index_info.indislive AS is_live,
    access_method.amname AS access_method,
    index_info.indnkeyatts AS key_attribute_count,
    index_info.indnatts AS total_attribute_count,
    (SELECT array_agg(pg_get_indexdef(index_info.indexrelid, key_number, TRUE)
                      ORDER BY key_number)
     FROM generate_series(1, index_info.indnkeyatts) AS key_number) AS indexed_columns_or_expressions,
    (SELECT array_agg(pg_get_indexdef(index_info.indexrelid, key_number, TRUE)
                      ORDER BY key_number)
     FROM generate_series(index_info.indnkeyatts + 1, index_info.indnatts) AS key_number)
        AS included_columns,
    pg_get_expr(index_info.indexprs, index_info.indrelid, TRUE) AS expression_definition,
    pg_get_expr(index_info.indpred, index_info.indrelid, TRUE) AS predicate,
    pg_get_indexdef(index_info.indexrelid) AS complete_definition
FROM (SELECT 1 AS anchor) AS anchor
LEFT JOIN target AS t ON TRUE
LEFT JOIN pg_index AS index_info ON index_info.indrelid = t.oid
LEFT JOIN pg_class AS index_relation ON index_relation.oid = index_info.indexrelid
LEFT JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
ORDER BY index_relation.relname NULLS LAST;

-- 4. Direct ACL entries and effective privileges for every role.
-- Migration 024's table ACL intent is service_role SELECT/INSERT/UPDATE only,
-- with PUBLIC, anon, and authenticated revoked. Owner privileges are reported
-- but are not treated as migration-added grants.
WITH target AS (
    SELECT c.oid, c.relowner, c.relacl
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
), principals AS (
    SELECT 0::oid AS role_oid, 'PUBLIC'::text AS role_name
    UNION ALL
    SELECT r.oid, r.rolname::text
    FROM pg_roles AS r
), privilege_names(privilege_name) AS (
    VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
           ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
    UNION ALL
    SELECT 'MAINTAIN'
    WHERE current_setting('server_version_num')::integer >= 170000
), direct_acl AS (
    SELECT
        acl.grantee,
        acl.privilege_type,
        bool_or(acl.is_grantable) AS is_grantable,
        string_agg(DISTINCT pg_get_userbyid(acl.grantor), ', ' ORDER BY pg_get_userbyid(acl.grantor))
            AS grantors
    FROM target AS t
    CROSS JOIN LATERAL aclexplode(
        COALESCE(t.relacl, acldefault('r', t.relowner))
    ) AS acl
    GROUP BY acl.grantee, acl.privilege_type
)
SELECT
    t.oid IS NOT NULL AS table_exists,
    p.role_name AS grantee,
    privilege.privilege_name AS privilege,
    COALESCE(direct_acl.grantee IS NOT NULL, FALSE) AS directly_granted,
    CASE
        WHEN p.role_oid = 0 THEN COALESCE(direct_acl.grantee IS NOT NULL, FALSE)
        ELSE has_table_privilege(p.role_oid, t.oid, privilege.privilege_name)
    END AS effectively_available,
    COALESCE(direct_acl.is_grantable, FALSE) AS grantable,
    direct_acl.grantors AS grantor,
    CASE
        WHEN p.role_oid = t.relowner THEN 'OWNER PRIVILEGE; NOT ADDED BY 024'
        WHEN p.role_name = 'service_role'
             AND privilege.privilege_name IN ('SELECT', 'INSERT', 'UPDATE')
             AND direct_acl.grantee IS NOT NULL
             AND NOT COALESCE(direct_acl.is_grantable, FALSE)
            THEN 'MATCHES 024'
        WHEN p.role_name = 'service_role'
             AND privilege.privilege_name IN ('SELECT', 'INSERT', 'UPDATE')
             AND direct_acl.grantee IS NULL
            THEN 'MISSING EXPECTED DIRECT GRANT'
        WHEN p.role_name = 'service_role' AND direct_acl.grantee IS NOT NULL
            THEN 'EXTRA SERVICE_ROLE GRANT OR GRANT OPTION'
        WHEN p.role_name IN ('PUBLIC', 'anon', 'authenticated')
             AND direct_acl.grantee IS NOT NULL
            THEN 'UNEXPECTED GRANT; 024 REVOKES THIS'
        WHEN direct_acl.grantee IS NOT NULL
            THEN 'OTHER DIRECT GRANT; REVIEW AGAINST 024 CONTRACT'
        WHEN p.role_oid <> 0
             AND has_table_privilege(p.role_oid, t.oid, privilege.privilege_name)
            THEN 'EFFECTIVE THROUGH PUBLIC OR ROLE MEMBERSHIP'
        ELSE 'NO DIRECT GRANT'
    END AS migration_024_comparison,
    CASE WHEN t.relacl IS NULL THEN 'DEFAULT ACL' ELSE 'EXPLICIT ACL' END AS acl_source
FROM target AS t
CROSS JOIN principals AS p
CROSS JOIN privilege_names AS privilege
LEFT JOIN direct_acl
  ON direct_acl.grantee = p.role_oid
 AND direct_acl.privilege_type = privilege.privilege_name
ORDER BY p.role_name, privilege.privilege_name;

-- 5. Every public overload, including definition, security, owner, ACL, and
-- catalog-tracked references/dependencies. SQL body dependencies may not all
-- be represented in pg_depend; result set 7 also reports source-text candidates.
WITH functions AS (
    SELECT p.*, n.nspname AS schema_name, language.lanname AS language_name
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    JOIN pg_language AS language ON language.oid = p.prolang
    WHERE n.nspname = 'public'
      AND p.proname = 'search_demand_embedding_candidates_v1'
)
SELECT
    f.oid IS NOT NULL AS function_exists,
    f.schema_name AS schema_name,
    f.proname AS function_name,
    pg_get_function_identity_arguments(f.oid) AS identity_arguments,
    f.proargnames AS argument_and_output_names,
    pg_get_function_result(f.oid) AS return_type,
    CASE f.prokind
        WHEN 'f' THEN 'FUNCTION'
        WHEN 'p' THEN 'PROCEDURE'
        WHEN 'a' THEN 'AGGREGATE'
        WHEN 'w' THEN 'WINDOW FUNCTION'
        ELSE f.prokind::text
    END AS routine_kind,
    CASE WHEN f.prokind IN ('f', 'p') THEN pg_get_functiondef(f.oid) END AS complete_definition,
    f.language_name AS language,
    CASE f.provolatile
        WHEN 'i' THEN 'IMMUTABLE'
        WHEN 's' THEN 'STABLE'
        WHEN 'v' THEN 'VOLATILE'
    END AS volatility,
    f.prosecdef AS security_definer,
    CASE WHEN f.prosecdef THEN 'DEFINER' ELSE 'INVOKER' END AS security_mode,
    (SELECT string_agg(setting, ', ' ORDER BY setting)
     FROM unnest(f.proconfig) AS setting
     WHERE setting LIKE 'search_path=%') AS search_path_configuration,
    pg_get_userbyid(f.proowner) AS owner,
    (SELECT string_agg(
                (CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                      ELSE pg_get_userbyid(acl.grantee) END)
                || ':' || acl.privilege_type
                || CASE WHEN acl.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END
                || ' GRANTED BY ' || pg_get_userbyid(acl.grantor),
                '; ' ORDER BY acl.grantee, acl.privilege_type, acl.grantor
            )
     FROM aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) AS acl
    ) AS direct_acl,
    (SELECT string_agg(
                pg_describe_object(dependency.refclassid, dependency.refobjid,
                                   dependency.refobjsubid)
                || ' [' || dependency.deptype || ']',
                '; ' ORDER BY dependency.refclassid, dependency.refobjid,
                             dependency.refobjsubid
            )
     FROM pg_depend AS dependency
     WHERE dependency.classid = 'pg_proc'::regclass
       AND dependency.objid = f.oid
    ) AS catalog_dependencies,
    (SELECT string_agg(
                pg_describe_object(dependency.classid, dependency.objid,
                                   dependency.objsubid)
                || ' [' || dependency.deptype || ']',
                '; ' ORDER BY dependency.classid, dependency.objid,
                             dependency.objsubid
            )
     FROM pg_depend AS dependency
     WHERE dependency.refclassid = 'pg_proc'::regclass
       AND dependency.refobjid = f.oid
    ) AS catalog_dependents
FROM (SELECT 1 AS anchor) AS anchor
LEFT JOIN functions AS f ON TRUE
ORDER BY f.oid NULLS LAST;

-- 6. Exact visible row count and data-quality counts without returning vectors.
-- query_to_xml executes only the generated SELECT. Expressions are selected
-- from catalog-confirmed columns, so absent/mismatched columns do not abort it.
WITH target AS (
    SELECT c.oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
), column_presence AS (
    SELECT
        COALESCE(bool_or(a.attname = 'demand_id'), FALSE) AS has_demand_id,
        COALESCE(bool_or(a.attname = 'embedding'), FALSE) AS has_embedding,
        COALESCE(bool_or(a.attname = 'embedding_model'), FALSE) AS has_embedding_model,
        COALESCE(bool_or(a.attname = 'embedding_version'), FALSE) AS has_embedding_version,
        COALESCE(bool_or(a.attname = 'input_hash'), FALSE) AS has_input_hash,
        COALESCE(bool_or(a.attname = 'embedding'
                         AND a.atttypid = vector_type.oid), FALSE) AS embedding_is_vector,
        vector_namespace.nspname AS vector_schema
    FROM (SELECT 1 AS anchor) AS anchor
    LEFT JOIN target AS t ON TRUE
    LEFT JOIN pg_attribute AS a
      ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_extension AS vector_extension ON vector_extension.extname = 'vector'
    LEFT JOIN pg_namespace AS vector_namespace
      ON vector_namespace.oid = vector_extension.extnamespace
    LEFT JOIN pg_type AS vector_type
      ON vector_type.typnamespace = vector_namespace.oid AND vector_type.typname = 'vector'
    GROUP BY vector_namespace.nspname
), vector_dimensions_function AS (
    SELECT EXISTS (
        SELECT 1
        FROM pg_extension AS e
        JOIN pg_proc AS p ON p.pronamespace = e.extnamespace
        JOIN pg_type AS vector_type ON vector_type.typnamespace = e.extnamespace
                                  AND vector_type.typname = 'vector'
        WHERE e.extname = 'vector' AND p.proname = 'vector_dims'
          AND p.pronargs = 1 AND p.proargtypes[0] = vector_type.oid
    ) AS available
), query_source AS (
    SELECT
        t.oid IS NOT NULL AS table_exists,
        cp.embedding_is_vector AND vdf.available AS vector_dimensions_available,
        CASE WHEN t.oid IS NULL THEN NULL
             ELSE 'SELECT count(*)::bigint AS total_rows, '
               || CASE WHEN cp.has_demand_id
                       THEN 'count(*) FILTER (WHERE demand_id IS NULL)::bigint'
                       ELSE 'NULL::bigint' END
               || ' AS null_demand_ids, '
               || CASE WHEN cp.has_embedding
                       THEN 'count(*) FILTER (WHERE embedding IS NULL)::bigint'
                       ELSE 'NULL::bigint' END
               || ' AS null_embeddings, '
               || CASE WHEN cp.embedding_is_vector AND vdf.available
                       THEN format(
                           '(SELECT string_agg(dimensions::text, '','' ORDER BY dimensions) '
                           || 'FROM (SELECT DISTINCT %I.vector_dims(embedding) AS dimensions '
                           || 'FROM public.demand_embeddings WHERE embedding IS NOT NULL) AS dims)',
                           cp.vector_schema
                       )
                       ELSE 'NULL::text' END
               || ' AS vector_dimensions, '
               || CASE WHEN cp.has_embedding_model
                       THEN 'count(*) FILTER (WHERE embedding_model IS NULL OR btrim(embedding_model::text) = '''')::bigint'
                       ELSE 'NULL::bigint' END
               || ' AS null_or_empty_models, '
               || CASE WHEN cp.has_embedding_version
                       THEN 'count(*) FILTER (WHERE embedding_version IS NULL OR btrim(embedding_version::text) = '''')::bigint'
                       ELSE 'NULL::bigint' END
               || ' AS null_or_empty_versions, '
               || CASE WHEN cp.has_input_hash
                       THEN 'count(*) FILTER (WHERE input_hash IS NULL OR btrim(input_hash::text) = '''')::bigint'
                       ELSE 'NULL::bigint' END
               || ' AS null_or_empty_hashes, '
               || CASE WHEN cp.has_demand_id
                       THEN '(SELECT string_agg(duplicate_id || '' ('' || duplicate_count::text || '')'', '', '' ORDER BY duplicate_id) '
                           || 'FROM (SELECT demand_id::text AS duplicate_id, count(*) AS duplicate_count '
                           || 'FROM public.demand_embeddings WHERE demand_id IS NOT NULL '
                           || 'GROUP BY demand_id::text HAVING count(*) > 1) AS duplicates)'
                       ELSE 'NULL::text' END
               || ' AS duplicate_demand_ids '
               || 'FROM public.demand_embeddings'
        END AS inspection_query
    FROM (SELECT 1 AS anchor) AS anchor
    LEFT JOIN target AS t ON TRUE
    CROSS JOIN column_presence AS cp
    CROSS JOIN vector_dimensions_function AS vdf
), inspection AS (
    SELECT
        table_exists,
        vector_dimensions_available,
        CASE WHEN table_exists
             THEN query_to_xml(inspection_query, TRUE, FALSE, '')
        END AS data_xml
    FROM query_source
)
SELECT
    table_exists,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/total_rows/text()', data_xml))[1]::text, '')::bigint
    END AS total_rows,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/null_demand_ids/text()', data_xml))[1]::text, '')::bigint
    END AS null_demand_ids,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/null_embeddings/text()', data_xml))[1]::text, '')::bigint
    END AS null_embeddings,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/vector_dimensions/text()', data_xml))[1]::text, '')
    END AS observed_vector_dimensions,
    vector_dimensions_available,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/null_or_empty_models/text()', data_xml))[1]::text, '')::bigint
    END AS null_or_empty_models,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/null_or_empty_versions/text()', data_xml))[1]::text, '')::bigint
    END AS null_or_empty_versions,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/null_or_empty_hashes/text()', data_xml))[1]::text, '')::bigint
    END AS null_or_empty_hashes,
    CASE WHEN data_xml IS NOT NULL THEN
        NULLIF((xpath('/table/row/duplicate_demand_ids/text()', data_xml))[1]::text, '')
    END AS duplicate_demand_ids
FROM inspection;

-- 7. Catalog dependencies, table triggers, and source-text function candidates.
-- Source-text matches are heuristic; dynamic SQL and some SQL-language bodies
-- are not fully represented by PostgreSQL dependency catalogs.
WITH target_table AS (
    SELECT c.oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
), target_functions AS (
    SELECT p.oid
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname = 'search_demand_embedding_candidates_v1'
), catalog_dependencies AS (
    SELECT
        'public.demand_embeddings table'::text AS dependency_target,
        pg_describe_object(d.classid, d.objid, d.objsubid) AS dependent_object,
        d.deptype::text AS dependency_type,
        'pg_depend catalog'::text AS discovery_method
    FROM pg_depend AS d
    JOIN target_table AS t
      ON d.refclassid = 'pg_class'::regclass AND d.refobjid = t.oid
    UNION ALL
    SELECT
        'public.search_demand_embedding_candidates_v1 RPC'::text,
        pg_describe_object(d.classid, d.objid, d.objsubid),
        d.deptype::text,
        'pg_depend catalog'
    FROM pg_depend AS d
    JOIN target_functions AS f
      ON d.refclassid = 'pg_proc'::regclass AND d.refobjid = f.oid
), table_triggers AS (
    SELECT
        'public.demand_embeddings table'::text AS dependency_target,
        pg_describe_object('pg_trigger'::regclass, trigger.oid, 0)
            || ' [' || pg_get_triggerdef(trigger.oid, TRUE) || ']' AS dependent_object,
        NULL::text AS dependency_type,
        'pg_trigger catalog'::text AS discovery_method
    FROM pg_trigger AS trigger
    JOIN target_table AS t ON t.oid = trigger.tgrelid
    WHERE NOT trigger.tgisinternal
    UNION ALL
    SELECT
        'public.demand_embeddings table'::text,
        pg_describe_object('pg_proc'::regclass, trigger.tgfoid, 0),
        NULL::text,
        'trigger handler function'
    FROM pg_trigger AS trigger
    JOIN target_table AS t ON t.oid = trigger.tgrelid
    WHERE NOT trigger.tgisinternal
), source_candidates AS (
    SELECT
        'demand_embeddings / search RPC source candidate'::text AS dependency_target,
        pg_describe_object('pg_proc'::regclass, p.oid, 0)
            || ' [source text mentions target name]' AS dependent_object,
        NULL::text AS dependency_type,
        'function source-text heuristic'::text AS discovery_method
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND (position('demand_embeddings' IN lower(p.prosrc)) > 0
           OR position('search_demand_embedding_candidates_v1' IN lower(p.prosrc)) > 0)
)
SELECT dependency_target, dependent_object, dependency_type, discovery_method
FROM catalog_dependencies
UNION ALL
SELECT dependency_target, dependent_object, dependency_type, discovery_method
FROM table_triggers
UNION ALL
SELECT dependency_target, dependent_object, dependency_type, discovery_method
FROM source_candidates
ORDER BY dependency_target, dependent_object;

COMMIT;