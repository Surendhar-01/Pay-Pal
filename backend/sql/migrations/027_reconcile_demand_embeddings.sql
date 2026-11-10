-- DRAFT: reconcile the existing demand embedding objects to the 024 behavior.
-- Run only after reviewing the preconditions below against the target database.
-- Existing rows, the created_at column/default, metadata defaults, primary key,
-- and the validated demand_id -> demands(id) ON DELETE CASCADE FK are preserved.
-- The FK is intentionally retained; this migration does not infer a reason to
-- change established demand-deletion behavior.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- Serialize the data checks and constraint/index reconciliation with writers.
DO $reconcile_demand_embeddings_024$
DECLARE
    v_table_oid OID;
    v_table_owner_oid OID;
    v_demands_oid OID;
    v_vector_namespace_oid OID;
    v_vector_oid OID;
    v_vector_schema NAME;
    v_table_owner NAME;
    v_service_role_oid OID;
    v_anon_oid OID;
    v_authenticated_oid OID;
    v_function_oid OID;
    v_demand_id_attnum SMALLINT;
    v_model_attnum SMALLINT;
    v_version_attnum SMALLINT;
    v_hash_attnum SMALLINT;
    v_rows_before BIGINT;
    v_rows_after BIGINT;
    v_invalid_rows BIGINT;
    v_index_oid OID;
    v_column_count INTEGER;
    v_current_is_superuser BOOLEAN;
BEGIN
    SELECT r.rolsuper INTO v_current_is_superuser
    FROM pg_roles AS r WHERE r.rolname = current_user;

        SELECT c.oid, c.relowner, pg_get_userbyid(c.relowner)::name
            INTO v_table_oid, v_table_owner_oid, v_table_owner
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings'
      AND c.relkind = 'r';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Preflight failed: public.demand_embeddings must exist as a regular table';
    END IF;

    IF v_table_owner = 'service_role' THEN
        RAISE EXCEPTION 'Preflight failed: service_role owns demand_embeddings; ACL normalization cannot remove owner privileges';
    END IF;
    IF current_user::text <> v_table_owner::text THEN
        RAISE EXCEPTION 'Preflight failed: run as the existing demand_embeddings owner so ACL grantors remain unchanged';
    END IF;

    EXECUTE 'LOCK TABLE public.demand_embeddings IN ACCESS EXCLUSIVE MODE';
    IF to_regclass('public.demand_embeddings')::oid <> v_table_oid THEN
        RAISE EXCEPTION 'Preflight failed: public.demand_embeddings changed while acquiring the migration lock';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION 'Preflight failed: required Supabase roles are missing';
    END IF;
    SELECT oid INTO v_service_role_oid FROM pg_roles WHERE rolname = 'service_role';
    SELECT oid INTO v_anon_oid FROM pg_roles WHERE rolname = 'anon';
    SELECT oid INTO v_authenticated_oid FROM pg_roles WHERE rolname = 'authenticated';

    SELECT count(*) INTO v_column_count
    FROM pg_attribute
    WHERE attrelid = v_table_oid AND attnum > 0 AND NOT attisdropped;
    IF v_column_count <> 7 OR EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = v_table_oid AND attnum > 0 AND NOT attisdropped
          AND attname NOT IN (
              'demand_id', 'embedding', 'embedding_model', 'embedding_version',
              'input_hash', 'created_at', 'updated_at'
          )
    ) THEN
        RAISE EXCEPTION 'Preflight failed: demand_embeddings must have exactly the seven inspected columns';
    END IF;

    -- The detailed table ACL result set was not supplied. Only allow the
    -- observed Supabase grantees and owner-issued ACL entries; unknown grantees
    -- or grantors require review and are never silently revoked.
    IF EXISTS (
        SELECT 1
        FROM pg_class AS c
        CROSS JOIN LATERAL aclexplode(
            COALESCE(c.relacl, acldefault('r', c.relowner))
        ) AS acl
        WHERE c.oid = v_table_oid
          AND (acl.grantor <> v_table_owner_oid
               OR acl.grantee NOT IN (
                   0::oid, v_table_owner_oid, v_service_role_oid,
                   v_anon_oid, v_authenticated_oid
               ))
    ) THEN
        RAISE EXCEPTION 'Preflight failed: table ACL has an unexpected grantee or grantor; review the diagnostic privilege result set';
    END IF;

        SELECT e.extnamespace, n.nspname, t.oid
            INTO STRICT v_vector_namespace_oid, v_vector_schema, v_vector_oid
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    JOIN pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
    WHERE e.extname = 'vector';

    IF NOT EXISTS (
           SELECT 1 FROM pg_operator AS op
           WHERE op.oprnamespace = v_vector_namespace_oid
             AND op.oprname = '<=>'
             AND op.oprleft = v_vector_oid AND op.oprright = v_vector_oid
       ) OR NOT EXISTS (
           SELECT 1 FROM pg_proc AS p
           WHERE p.pronamespace = v_vector_namespace_oid
             AND p.proname = 'vector_dims' AND p.pronargs = 1
             AND p.proargtypes[0] = v_vector_oid
       ) THEN
        RAISE EXCEPTION 'Preflight failed: pgvector type, cosine operator, or vector_dims does not match the observed contract';
    END IF;

    SELECT c.oid INTO v_demands_oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demands' AND c.relkind IN ('r', 'p');
    IF v_demands_oid IS NULL THEN
        RAISE EXCEPTION 'Preflight failed: referenced table public.demands is missing';
    END IF;

    SELECT attnum INTO v_demand_id_attnum
    FROM pg_attribute
    WHERE attrelid = v_table_oid AND attname = 'demand_id'
      AND atttypid = 'uuid'::regtype AND attnotnull
      AND attnum > 0 AND NOT attisdropped;
    SELECT attnum INTO v_model_attnum
    FROM pg_attribute
    WHERE attrelid = v_table_oid AND attname = 'embedding_model'
      AND atttypid = 'text'::regtype AND attnotnull
      AND attnum > 0 AND NOT attisdropped;
    SELECT attnum INTO v_version_attnum
    FROM pg_attribute
    WHERE attrelid = v_table_oid AND attname = 'embedding_version'
      AND atttypid = 'text'::regtype AND attnotnull
      AND attnum > 0 AND NOT attisdropped;
    SELECT attnum INTO v_hash_attnum
    FROM pg_attribute
    WHERE attrelid = v_table_oid AND attname = 'input_hash'
      AND atttypid = 'text'::regtype AND attnotnull
      AND attnum > 0 AND NOT attisdropped;

    IF v_demand_id_attnum IS NULL OR v_model_attnum IS NULL
       OR v_version_attnum IS NULL OR v_hash_attnum IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute
           WHERE attrelid = v_table_oid AND attname = 'embedding'
                         AND atttypid = v_vector_oid
                         AND format_type(atttypid, atttypmod) ~ '(^|[.])vector\(768\)$'
             AND attnotnull AND attnum > 0 AND NOT attisdropped
       )
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute AS a
           JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           WHERE a.attrelid = v_table_oid AND a.attname = 'embedding_model'
             AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g')
                                 = quote_literal('nomic-embed-text') || '::text'
       )
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute AS a
           JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           WHERE a.attrelid = v_table_oid AND a.attname = 'embedding_version'
             AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g')
                                 = quote_literal('v1') || '::text'
       )
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute AS a
           JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           WHERE a.attrelid = v_table_oid AND a.attname = 'updated_at'
             AND a.atttypid = 'timestamptz'::regtype AND a.attnotnull
             AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g') = 'now'
       )
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute AS a
           JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           WHERE a.attrelid = v_table_oid AND a.attname = 'created_at'
             AND a.atttypid = 'timestamptz'::regtype AND a.attnotnull
             AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g') = 'now'
       )
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute AS a
           WHERE a.attrelid = v_table_oid AND a.attname = 'input_hash'
             AND NOT EXISTS (
                 SELECT 1 FROM pg_attrdef AS d
                 WHERE d.adrelid = a.attrelid AND d.adnum = a.attnum
             )
       ) OR EXISTS (
           SELECT 1 FROM pg_attribute AS a
           JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           WHERE a.attrelid = v_table_oid AND a.attname IN ('demand_id', 'embedding')
       ) THEN
        RAISE EXCEPTION 'Preflight failed: existing columns, types, nullability, or defaults differ from the inspected schema';
    END IF;

    -- Preserve and verify the existing PK and cascade FK; never drop either.
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid = v_table_oid AND contype = 'p') <> 1
       OR NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           WHERE c.conrelid = v_table_oid AND c.conname = 'demand_embeddings_pkey'
             AND c.contype = 'p' AND c.convalidated AND NOT c.condeferrable
             AND cardinality(c.conkey) = 1 AND c.conkey[1] = v_demand_id_attnum
       ) THEN
        RAISE EXCEPTION 'Preflight failed: existing demand_embeddings primary key differs from the inspected primary key';
    END IF;

    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid = v_table_oid AND contype = 'f') <> 1
       OR NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           JOIN pg_attribute AS referenced_column
             ON referenced_column.attrelid = c.confrelid
            AND referenced_column.attname = 'id'
           WHERE c.conrelid = v_table_oid
             AND c.conname = 'demand_embeddings_demand_id_fkey'
             AND c.contype = 'f' AND c.confrelid = v_demands_oid
             AND cardinality(c.conkey) = 1 AND c.conkey[1] = v_demand_id_attnum
             AND cardinality(c.confkey) = 1 AND c.confkey[1] = referenced_column.attnum
             AND c.confdeltype = 'c' AND c.confupdtype = 'a'
             AND c.convalidated AND NOT c.condeferrable
       ) THEN
        RAISE EXCEPTION 'Preflight failed: demand_id FK differs from the observed validated public.demands(id) ON DELETE CASCADE contract; it will not be changed';
    END IF;

    -- Only the three expected checks may already exist; an unexpected check
    -- or other constraint requires explicit review rather than replacement.
    IF EXISTS (
        SELECT 1 FROM pg_constraint AS c
        WHERE c.conrelid = v_table_oid
          AND c.contype NOT IN ('p', 'f', 'c')
    ) OR EXISTS (
        SELECT 1 FROM pg_constraint AS c
        WHERE c.conrelid = v_table_oid AND c.contype = 'c'
          AND NOT (
              (c.conname = 'demand_embeddings_embedding_model_check'
               AND c.convalidated
               AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                  '[[:space:]()]|::text', '', 'g')
                   = 'embedding_model<>' || chr(39) || chr(39))
              OR (c.conname = 'demand_embeddings_embedding_version_check'
                  AND c.convalidated
                  AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                     '[[:space:]()]|::text', '', 'g')
                      = 'embedding_version<>' || chr(39) || chr(39))
              OR (c.conname = 'demand_embeddings_input_hash_check'
                  AND c.convalidated
                  AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                     '[[:space:]()]|::text', '', 'g')
                      = 'input_hash<>' || chr(39) || chr(39))
          )
    ) THEN
        RAISE EXCEPTION 'Preflight failed: an unexpected or incompatible demand_embeddings constraint exists';
    END IF;

    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = v_table_oid)
       OR ((SELECT relforcerowsecurity FROM pg_class WHERE oid = v_table_oid)
           AND NOT COALESCE(v_current_is_superuser, FALSE))
       OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = v_table_oid) THEN
        RAISE EXCEPTION 'Preflight failed: expected RLS-enabled, policy-free table state differs';
    END IF;

    -- Check existing data before enforcing the 024 non-empty and vector rules.
    SELECT count(*) INTO v_rows_before FROM public.demand_embeddings;
    EXECUTE format($query$
        SELECT count(*)
        FROM public.demand_embeddings
        WHERE demand_id IS NULL OR embedding IS NULL
           OR embedding_model IS NULL OR btrim(embedding_model) = ''
           OR embedding_version IS NULL OR btrim(embedding_version) = ''
           OR input_hash IS NULL OR btrim(input_hash) = ''
           OR %1$I.vector_dims(embedding) <> 768
    $query$, v_vector_schema) INTO v_invalid_rows;
    IF v_invalid_rows <> 0 THEN
        RAISE EXCEPTION 'Preflight failed: % existing embedding rows violate the required data contract', v_invalid_rows;
    END IF;

    -- Add only missing expected checks; validate each before commit.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = v_table_oid
                   AND conname = 'demand_embeddings_embedding_model_check') THEN
        ALTER TABLE public.demand_embeddings
            ADD CONSTRAINT demand_embeddings_embedding_model_check
            CHECK (embedding_model <> '') NOT VALID;
    END IF;
    ALTER TABLE public.demand_embeddings
        VALIDATE CONSTRAINT demand_embeddings_embedding_model_check;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = v_table_oid
                   AND conname = 'demand_embeddings_embedding_version_check') THEN
        ALTER TABLE public.demand_embeddings
            ADD CONSTRAINT demand_embeddings_embedding_version_check
            CHECK (embedding_version <> '') NOT VALID;
    END IF;
    ALTER TABLE public.demand_embeddings
        VALIDATE CONSTRAINT demand_embeddings_embedding_version_check;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = v_table_oid
                   AND conname = 'demand_embeddings_input_hash_check') THEN
        ALTER TABLE public.demand_embeddings
            ADD CONSTRAINT demand_embeddings_input_hash_check
            CHECK (input_hash <> '') NOT VALID;
    END IF;
    ALTER TABLE public.demand_embeddings
        VALIDATE CONSTRAINT demand_embeddings_input_hash_check;

    -- The supplied index inventory contains only the PK index. If a named
    -- object exists now, require it to be the exact expected valid btree.
    SELECT c.oid INTO v_index_oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'demand_embeddings_model_version_idx';
    IF v_index_oid IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_index AS i
        JOIN pg_class AS index_class ON index_class.oid = i.indexrelid
        JOIN pg_am AS am ON am.oid = index_class.relam
        WHERE i.indexrelid = v_index_oid AND i.indrelid = v_table_oid
          AND i.indisvalid AND i.indisready AND i.indislive
          AND NOT i.indisunique AND i.indpred IS NULL
          AND i.indnkeyatts = 2 AND i.indnatts = 2 AND am.amname = 'btree'
          AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'embedding_model'
          AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'embedding_version'
    ) THEN
        RAISE EXCEPTION 'Preflight failed: demand_embeddings_model_version_idx exists but is incompatible';
    END IF;
    IF v_index_oid IS NULL THEN
        CREATE INDEX demand_embeddings_model_version_idx
            ON public.demand_embeddings USING btree (embedding_model, embedding_version);
    END IF;

    -- Require the observed single overload, stable return contract, owner, and
    -- no catalog dependents. CREATE OR REPLACE keeps the same function OID and
    -- argument/return contract; it does not drop the routine.
    SELECT p.oid INTO v_function_oid
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname = 'search_demand_embedding_candidates_v1'
      AND p.pronargs = 6
      AND p.proargtypes[0] = v_vector_oid
      AND p.proargtypes[1] = 'uuid'::regtype
      AND p.proargtypes[2] = 'integer'::regtype
      AND p.proargtypes[3] = 'double precision'::regtype
      AND p.proargtypes[4] = 'text'::regtype
      AND p.proargtypes[5] = 'text'::regtype;
    IF v_function_oid IS NULL
       OR (SELECT count(*) FROM pg_proc AS p
           JOIN pg_namespace AS n ON n.oid = p.pronamespace
           WHERE n.nspname = 'public'
             AND p.proname = 'search_demand_embedding_candidates_v1') <> 1
       OR NOT EXISTS (
           SELECT 1 FROM pg_proc AS p
           WHERE p.oid = v_function_oid
             AND p.prorettype = 'record'::regtype AND p.proretset
             AND p.proargmodes::text = '{i,i,i,i,i,i,t,t}'
             AND p.proargnames = ARRAY[
                 'p_query_embedding', 'p_exclude_demand_id', 'p_candidate_limit',
                 'p_max_cosine_distance', 'p_embedding_model', 'p_embedding_version',
                 'demand_id', 'cosine_distance'
             ]::text[]
             AND p.provolatile = 's' AND NOT p.prosecdef
             AND p.proconfig = ARRAY['search_path=""']
             AND EXISTS (
                 SELECT 1 FROM pg_language AS language
                 WHERE language.oid = p.prolang AND language.lanname = 'plpgsql'
             )
             AND pg_get_function_result(p.oid)
                 = 'TABLE(demand_id uuid, cosine_distance double precision)'
             AND pg_get_userbyid(p.proowner) = 'postgres'
       ) THEN
        RAISE EXCEPTION 'Preflight failed: existing RPC signature, return contract, overload count, or owner differs from inspected state';
    END IF;
    IF current_user::text <> (
           SELECT pg_get_userbyid(p.proowner)
           FROM pg_proc AS p WHERE p.oid = v_function_oid
       )::text THEN
        RAISE EXCEPTION 'Preflight failed: run as the existing RPC owner so replacement preserves ownership';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS p
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f', p.proowner))
        ) AS acl
        WHERE p.oid = v_function_oid
          AND (acl.grantor <> p.proowner
               OR acl.grantee NOT IN (p.proowner, v_service_role_oid))
    ) THEN
        RAISE EXCEPTION 'Preflight failed: RPC ACL has an unexpected grantee or grantor';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_depend AS d
        WHERE d.refclassid = 'pg_proc'::regclass AND d.refobjid = v_function_oid
    ) THEN
        RAISE EXCEPTION 'Preflight failed: catalog dependents of the search RPC now exist; replacement requires review';
    END IF;

    -- The supplied function is PL/pgSQL, STABLE, INVOKER, and has
    -- search_path="". Its body validates inputs and executes only RETURN QUERY
    -- SELECT; the backend caller validates those same inputs before invoking it.
    -- Transition it explicitly to the 024 SQL/STABLE/INVOKER contract and
    -- search_path=pg_catalog, public. The signature, return shape, owner, and
    -- catalog dependency state were verified above; do not DROP the function.
    -- Resolve pgvector in its extension schema and qualify its operator/function.
    EXECUTE format($ddl$
        CREATE OR REPLACE FUNCTION public.search_demand_embedding_candidates_v1(
            p_query_embedding %1$I.vector(768),
            p_exclude_demand_id UUID,
            p_candidate_limit INTEGER,
            p_max_cosine_distance DOUBLE PRECISION,
            p_embedding_model TEXT,
            p_embedding_version TEXT
        )
        RETURNS TABLE(demand_id UUID, cosine_distance DOUBLE PRECISION)
        LANGUAGE SQL
        STABLE
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT stored.demand_id,
                   (stored.embedding OPERATOR(%1$I.<=>) p_query_embedding)::DOUBLE PRECISION
                       AS cosine_distance
            FROM public.demand_embeddings AS stored
            WHERE p_query_embedding IS NOT NULL
              AND %1$I.vector_dims(p_query_embedding) = 768
              AND p_candidate_limit BETWEEN 1 AND 100
              AND p_max_cosine_distance BETWEEN 0 AND 2
              AND p_embedding_model IS NOT NULL
              AND btrim(p_embedding_model) <> ''
              AND p_embedding_version IS NOT NULL
              AND btrim(p_embedding_version) <> ''
              AND stored.embedding_model = p_embedding_model
              AND stored.embedding_version = p_embedding_version
              AND stored.demand_id IS DISTINCT FROM p_exclude_demand_id
              AND stored.embedding OPERATOR(%1$I.<=>) p_query_embedding
                    <= p_max_cosine_distance
            ORDER BY stored.embedding OPERATOR(%1$I.<=>) p_query_embedding,
                     stored.demand_id
            LIMIT CASE
                WHEN p_candidate_limit BETWEEN 1 AND 100 THEN p_candidate_limit
                ELSE 0
            END
        $function$
    $ddl$, v_vector_schema);

    -- Normalize direct table ACLs. The table owner is preserved and required
    -- service_role SELECT/INSERT/UPDATE access is restored explicitly.
    REVOKE ALL ON TABLE public.demand_embeddings
        FROM PUBLIC, anon, authenticated, service_role;
    GRANT SELECT, INSERT, UPDATE ON TABLE public.demand_embeddings TO service_role;

    EXECUTE format(
        'REVOKE ALL ON FUNCTION public.search_demand_embedding_candidates_v1(%1$I.vector(768), UUID, INTEGER, DOUBLE PRECISION, TEXT, TEXT) FROM PUBLIC, anon, authenticated, service_role',
        v_vector_schema
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION public.search_demand_embedding_candidates_v1(%1$I.vector(768), UUID, INTEGER, DOUBLE PRECISION, TEXT, TEXT) TO service_role',
        v_vector_schema
    );

    -- Postconditions: exact 024 columns/behavior plus the known preserved
    -- created_at column and cascade FK. Any failure aborts the transaction.
    SELECT count(*) INTO v_column_count
    FROM pg_attribute
    WHERE attrelid = v_table_oid AND attnum > 0 AND NOT attisdropped;
    IF v_column_count <> 7 OR EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = v_table_oid AND attnum > 0 AND NOT attisdropped
          AND attname NOT IN (
              'demand_id', 'embedding', 'embedding_model', 'embedding_version',
              'input_hash', 'created_at', 'updated_at'
          )
    ) THEN
        RAISE EXCEPTION 'Postcondition failed: demand_embeddings no longer has exactly the seven inspected columns';
    END IF;

    IF (SELECT count(*) FROM pg_attribute AS a
        LEFT JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = v_table_oid AND a.attnum > 0 AND NOT a.attisdropped
          AND (
              (a.attname = 'demand_id' AND a.atttypid = 'uuid'::regtype
               AND a.attnotnull AND d.oid IS NULL)
              OR (a.attname = 'embedding' AND a.atttypid = v_vector_oid
                  AND format_type(a.atttypid, a.atttypmod) ~ '(^|[.])vector\(768\)$'
                  AND a.attnotnull AND d.oid IS NULL)
              OR (a.attname = 'embedding_model' AND a.atttypid = 'text'::regtype
                  AND a.attnotnull
                  AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g')
                      = quote_literal('nomic-embed-text') || '::text')
              OR (a.attname = 'embedding_version' AND a.atttypid = 'text'::regtype
                  AND a.attnotnull
                  AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g')
                      = quote_literal('v1') || '::text')
              OR (a.attname = 'input_hash' AND a.atttypid = 'text'::regtype
                  AND a.attnotnull AND d.oid IS NULL)
              OR (a.attname = 'created_at' AND a.atttypid = 'timestamptz'::regtype
                  AND a.attnotnull
                  AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g') = 'now')
              OR (a.attname = 'updated_at' AND a.atttypid = 'timestamptz'::regtype
                  AND a.attnotnull
                  AND regexp_replace(lower(pg_get_expr(d.adbin, d.adrelid)), '[[:space:]()]', '', 'g') = 'now')
          )) <> 7 THEN
        RAISE EXCEPTION 'Postcondition failed: one or more observed column types, nullability, or defaults changed';
    END IF;
    IF NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           WHERE c.conrelid = v_table_oid AND c.conname = 'demand_embeddings_pkey'
             AND c.contype = 'p' AND c.convalidated
             AND cardinality(c.conkey) = 1 AND c.conkey[1] = v_demand_id_attnum
       ) OR NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           WHERE c.conrelid = v_table_oid AND c.conname = 'demand_embeddings_demand_id_fkey'
             AND c.contype = 'f' AND c.convalidated AND c.confrelid = v_demands_oid
             AND cardinality(c.conkey) = 1 AND c.conkey[1] = v_demand_id_attnum
             AND c.confdeltype = 'c' AND c.confupdtype = 'a'
       ) THEN
        RAISE EXCEPTION 'Postcondition failed: the existing primary key or cascade FK was changed';
    END IF;
    IF NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           WHERE c.conrelid = v_table_oid AND c.conname = 'demand_embeddings_embedding_model_check'
             AND c.contype = 'c' AND c.convalidated
             AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                '[[:space:]()]|::text', '', 'g')
                 = 'embedding_model<>' || chr(39) || chr(39)
       ) OR NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           WHERE c.conrelid = v_table_oid AND c.conname = 'demand_embeddings_embedding_version_check'
             AND c.contype = 'c' AND c.convalidated
             AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                '[[:space:]()]|::text', '', 'g')
                 = 'embedding_version<>' || chr(39) || chr(39)
       ) OR NOT EXISTS (
           SELECT 1 FROM pg_constraint AS c
           WHERE c.conrelid = v_table_oid AND c.conname = 'demand_embeddings_input_hash_check'
             AND c.contype = 'c' AND c.convalidated
             AND regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)),
                                '[[:space:]()]|::text', '', 'g')
                 = 'input_hash<>' || chr(39) || chr(39)
       ) OR NOT EXISTS (
           SELECT 1 FROM pg_index AS i
           JOIN pg_class AS index_class ON index_class.oid = i.indexrelid
           JOIN pg_am AS am ON am.oid = index_class.relam
           WHERE i.indexrelid = to_regclass('public.demand_embeddings_model_version_idx')
             AND i.indrelid = v_table_oid AND i.indisvalid AND i.indisready AND i.indislive
             AND NOT i.indisunique AND i.indpred IS NULL
             AND i.indnkeyatts = 2 AND i.indnatts = 2 AND am.amname = 'btree'
             AND pg_get_indexdef(i.indexrelid, 1, TRUE) = 'embedding_model'
             AND pg_get_indexdef(i.indexrelid, 2, TRUE) = 'embedding_version'
       ) OR NOT EXISTS (
           SELECT 1 FROM pg_class AS c
           WHERE c.oid = v_table_oid AND c.relrowsecurity
       ) OR EXISTS (
           SELECT 1 FROM pg_policy AS policy WHERE policy.polrelid = v_table_oid
       ) THEN
        RAISE EXCEPTION 'Postcondition failed: checks, index, or RLS do not match the required contract';
    END IF;

    SELECT p.oid INTO v_function_oid
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname = 'search_demand_embedding_candidates_v1'
      AND p.pronargs = 6
      AND p.proargtypes[0] = v_vector_oid
      AND p.proargtypes[1] = 'uuid'::regtype
      AND p.proargtypes[2] = 'integer'::regtype
      AND p.proargtypes[3] = 'double precision'::regtype
      AND p.proargtypes[4] = 'text'::regtype
      AND p.proargtypes[5] = 'text'::regtype;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc AS p
        WHERE p.oid = v_function_oid AND p.prorettype = 'record'::regtype
          AND p.proretset AND p.provolatile = 's' AND NOT p.prosecdef
          AND p.proargnames = ARRAY[
              'p_query_embedding', 'p_exclude_demand_id', 'p_candidate_limit',
              'p_max_cosine_distance', 'p_embedding_model', 'p_embedding_version',
              'demand_id', 'cosine_distance'
          ]::text[]
          AND pg_get_function_result(p.oid)
              = 'TABLE(demand_id uuid, cosine_distance double precision)'
          AND p.proconfig = ARRAY['search_path=pg_catalog, public']
          AND EXISTS (SELECT 1 FROM pg_language AS l WHERE l.oid = p.prolang AND l.lanname = 'sql')
          AND position('p_query_embeddingisnotnull' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('vector_dims(p_query_embedding)=768' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('p_candidate_limitbetween1and100' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('p_max_cosine_distancebetween0and2' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('stored.embedding_model=p_embedding_model' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('stored.embedding_version=p_embedding_version' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('btrim(p_embedding_model)<>' || chr(39) || chr(39) IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('btrim(p_embedding_version)<>' || chr(39) || chr(39) IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('stored.demand_idisdistinctfromp_exclude_demand_id' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('<=>' IN regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('<=p_max_cosine_distance' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('orderbystored.embeddingoperator(' || lower(v_vector_schema)
                       || '.<=>)p_query_embedding,stored.demand_id' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
          AND position('limitcasewhenp_candidate_limitbetween1and100thenp_candidate_limitelse0end' IN
                       regexp_replace(lower(p.prosrc), '[[:space:]]', '', 'g')) > 0
    ) THEN
        RAISE EXCEPTION 'Postcondition failed: search RPC definition or security contract does not match';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_class AS c
        CROSS JOIN LATERAL aclexplode(
            COALESCE(c.relacl, acldefault('r', c.relowner))
        ) AS acl
        WHERE c.oid = v_table_oid
          AND (acl.grantor <> v_table_owner_oid
               OR acl.grantee NOT IN (v_table_owner_oid, v_service_role_oid))
    ) OR EXISTS (
        SELECT 1 FROM aclexplode((SELECT relacl FROM pg_class WHERE oid = v_table_oid)) AS acl
        JOIN pg_roles AS r ON r.oid = acl.grantee
        WHERE r.rolname = 'service_role'
          AND (acl.privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE') OR acl.is_grantable)
    ) OR (SELECT count(*) FROM aclexplode(
              (SELECT relacl FROM pg_class WHERE oid = v_table_oid)
          ) AS acl
          JOIN pg_roles AS r ON r.oid = acl.grantee
                    WHERE r.rolname = 'service_role'
                        AND acl.grantor = v_table_owner_oid
            AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE')) <> 3 THEN
        RAISE EXCEPTION 'Postcondition failed: service_role table grants are not exactly SELECT, INSERT, UPDATE';
    END IF;
    IF EXISTS (
        SELECT 1 FROM aclexplode((SELECT relacl FROM pg_class WHERE oid = v_table_oid)) AS acl
        LEFT JOIN pg_roles AS r ON r.oid = acl.grantee
        WHERE (acl.grantee = 0 OR r.rolname IN ('anon', 'authenticated'))
          AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE',
                                                                         'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')
    ) OR has_table_privilege('anon', v_table_oid, 'SELECT')
      OR has_table_privilege('anon', v_table_oid, 'INSERT')
      OR has_table_privilege('anon', v_table_oid, 'UPDATE')
      OR has_table_privilege('anon', v_table_oid, 'DELETE')
      OR has_table_privilege('anon', v_table_oid, 'TRUNCATE')
      OR has_table_privilege('anon', v_table_oid, 'REFERENCES')
      OR has_table_privilege('anon', v_table_oid, 'TRIGGER')
      OR has_table_privilege('authenticated', v_table_oid, 'SELECT')
      OR has_table_privilege('authenticated', v_table_oid, 'INSERT')
      OR has_table_privilege('authenticated', v_table_oid, 'UPDATE')
      OR has_table_privilege('authenticated', v_table_oid, 'DELETE')
      OR has_table_privilege('authenticated', v_table_oid, 'TRUNCATE')
      OR has_table_privilege('authenticated', v_table_oid, 'REFERENCES')
      OR has_table_privilege('authenticated', v_table_oid, 'TRIGGER')
      OR has_table_privilege('service_role', v_table_oid, 'DELETE')
      OR has_table_privilege('service_role', v_table_oid, 'TRUNCATE')
      OR has_table_privilege('service_role', v_table_oid, 'REFERENCES')
      OR has_table_privilege('service_role', v_table_oid, 'TRIGGER') THEN
        RAISE EXCEPTION 'Postcondition failed: public/anon/authenticated table access is not revoked';
    END IF;
    IF current_setting('server_version_num')::integer >= 170000 THEN
        IF has_table_privilege('anon', v_table_oid, 'MAINTAIN')
           OR has_table_privilege('authenticated', v_table_oid, 'MAINTAIN')
           OR has_table_privilege('service_role', v_table_oid, 'MAINTAIN') THEN
            RAISE EXCEPTION 'Postcondition failed: an unexpected effective MAINTAIN privilege remains';
        END IF;
    END IF;

        IF EXISTS (
                SELECT 1 FROM pg_proc AS p
                CROSS JOIN LATERAL aclexplode(
                        COALESCE(p.proacl, acldefault('f', p.proowner))
                ) AS acl
                WHERE p.oid = v_function_oid
                    AND (acl.grantor <> p.proowner
                             OR acl.grantee NOT IN (p.proowner, v_service_role_oid))
        ) OR EXISTS (
                SELECT 1 FROM pg_proc AS p
                CROSS JOIN LATERAL aclexplode(p.proacl) AS acl
                WHERE p.oid = v_function_oid
                    AND acl.grantee = v_service_role_oid
                    AND (acl.privilege_type <> 'EXECUTE' OR acl.is_grantable)
        ) OR (SELECT count(*)
                    FROM pg_proc AS p
                    CROSS JOIN LATERAL aclexplode(p.proacl) AS acl
                    WHERE p.oid = v_function_oid
                        AND acl.grantee = v_service_role_oid
                        AND acl.grantor = p.proowner
                        AND acl.privilege_type = 'EXECUTE'
                        AND NOT acl.is_grantable) <> 1
            OR NOT has_function_privilege('service_role', v_function_oid, 'EXECUTE')
            OR has_function_privilege('anon', v_function_oid, 'EXECUTE')
            OR has_function_privilege('authenticated', v_function_oid, 'EXECUTE') THEN
        RAISE EXCEPTION 'Postcondition failed: search RPC execute grants are not service-role-only';
    END IF;

    SELECT count(*) INTO v_rows_after FROM public.demand_embeddings;
    IF v_rows_after <> v_rows_before THEN
        RAISE EXCEPTION 'Postcondition failed: embedding row count changed from % to %',
            v_rows_before, v_rows_after;
    END IF;
END;
$reconcile_demand_embeddings_024$;

-- Read-only post-migration verification result set.
SELECT
    (SELECT count(*) = 7 FROM pg_attribute AS a
     WHERE a.attrelid = to_regclass('public.demand_embeddings')
       AND a.attnum > 0 AND NOT a.attisdropped) AS observed_columns_preserved,
    (SELECT count(*) FROM public.demand_embeddings) AS preserved_embedding_rows,
    EXISTS (SELECT 1 FROM pg_constraint AS c
            WHERE c.conrelid = 'public.demand_embeddings'::regclass
              AND c.conname = 'demand_embeddings_demand_id_fkey'
              AND c.confdeltype = 'c' AND c.convalidated) AS cascade_fk_preserved,
    EXISTS (SELECT 1 FROM pg_constraint AS c
            WHERE c.conrelid = 'public.demand_embeddings'::regclass
              AND c.conname = 'demand_embeddings_embedding_model_check'
              AND c.convalidated) AS model_check_validated,
    EXISTS (SELECT 1 FROM pg_constraint AS c
            WHERE c.conrelid = 'public.demand_embeddings'::regclass
              AND c.conname = 'demand_embeddings_embedding_version_check'
              AND c.convalidated) AS version_check_validated,
    EXISTS (SELECT 1 FROM pg_constraint AS c
            WHERE c.conrelid = 'public.demand_embeddings'::regclass
              AND c.conname = 'demand_embeddings_input_hash_check'
              AND c.convalidated) AS hash_check_validated,
    to_regclass('public.demand_embeddings_model_version_idx') IS NOT NULL AS model_version_index_present,
    (SELECT relrowsecurity FROM pg_class
     WHERE oid = to_regclass('public.demand_embeddings')) AS rls_enabled,
    (SELECT pg_get_function_result(p.oid) =
            'TABLE(demand_id uuid, cosine_distance double precision)'
     FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname = 'search_demand_embedding_candidates_v1'
       AND p.pronargs = 6) AS rpc_return_contract_matches;

NOTIFY pgrst, 'reload schema';
COMMIT;