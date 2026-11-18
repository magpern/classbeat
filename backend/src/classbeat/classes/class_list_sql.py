"""The class list's one statement (ADR 0013 section 11, UI2): every row of a page with its saved
state, open job, mini-profile and flags. Split out of ``classbeat.classes.service``."""

from sqlalchemy import text

from classbeat.classes.class_sql import OPEN_JOB
from classbeat.classes.kinds import CLASS_SPORT_SQL
from classbeat.swedebeat_studio.song_list_model import SOURCE as STUDIO_SOURCE

CLASSES = text(f"""
SELECT c.id, c.title, c.created_at, c.updated_at, c.current_revision_id,
    cur.number AS current_revision_number,
    (SELECT count(*) FROM class_revisions r
        WHERE r.owner_id = c.owner_id AND r.class_id = c.id AND r.kind = 'draft') AS revision_count,
    oj.id AS open_job_id, oj.kind AS open_job_kind, oj.state AS open_job_state,
    coalesce(sv.n, 0) AS saved_count, sv.revision_number AS saved_revision_number,
    sv.updated_at AS saved_at, (sv.content_hash = cur.content_hash) AS saved_up_to_date,
    sv.updatable_id AS saved_workout_id, sv.verified AS saved_verified,
    (SELECT count(*) FROM saved_workouts d
        WHERE d.owner_id = c.owner_id AND d.class_id = c.id AND d.detached_at IS NOT NULL
    ) AS detached_count,
    coalesce(pf.profile, '[]'::jsonb) AS profile,
    {CLASS_SPORT_SQL} AS sport,
    -- ADR 0007 amendment item 10 (#530): only a stored source 'none' is soundless.
    coalesce(c.music_source ->> 'source' = 'none', false) AS soundless,
    -- ADR 0036 section 5 (#1173): only a stored source 'swedebeat_studio' is a studio class.
    coalesce(c.music_source ->> 'source' = '{STUDIO_SOURCE}', false) AS studio,
    -- ADR 0036 section 6: a SwedeBeat playlist or mix was (or may have been) written for the class.
    EXISTS (SELECT 1 FROM class_outputs o WHERE o.owner_id = c.owner_id AND o.class_id = c.id
        AND o.output IN ('swedebeat_playlist', 'swedebeat_mix')
        AND (o.state IN ('done', 'in_doubt', 'published_not_listed')
            OR o.external_id IS NOT NULL)) AS swedebeat_written,
    CASE WHEN jsonb_typeof(cur.provenance -> 'tss_approximate') = 'boolean'
        THEN (cur.provenance ->> 'tss_approximate')::boolean
        ELSE EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(pf.profile, '[]'::jsonb)) z
            WHERE z ->> 0 = '7') END AS tss_approximate
FROM classes c
LEFT JOIN class_revisions cur
    ON cur.owner_id = c.owner_id AND cur.class_id = c.id AND cur.id = c.current_revision_id
LEFT JOIN jobs oj
    ON oj.owner_id = c.owner_id AND oj.class_id = c.id AND oj.state IN {OPEN_JOB}
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_array(t.zone, t.sec) ORDER BY t.seg_ord, t.iv_ord) AS profile
    FROM (
        -- Only whole numbers are cast; an interval with any other zone or length is left out
        -- (a bad value must not fail the whole list).
        SELECT sg.ord AS seg_ord, iv.ord AS iv_ord,
            CASE WHEN jsonb_typeof(iv.i -> 'zone') = 'number'
                AND (iv.i ->> 'zone') ~ '^[0-9][0-9]?$' THEN (iv.i ->> 'zone')::int END AS zone,
            CASE WHEN jsonb_typeof(iv.i -> 'sec') = 'number'
                AND (iv.i ->> 'sec') ~ '^[0-9]+$' AND length(iv.i ->> 'sec') <= 6
                THEN (iv.i ->> 'sec')::int END AS sec
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(cur.content -> 'segments') = 'array'
            THEN cur.content -> 'segments' ELSE '[]'::jsonb END) WITH ORDINALITY AS sg(seg, ord)
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(sg.seg -> 'intervals') = 'array'
            THEN sg.seg -> 'intervals' ELSE '[]'::jsonb END) WITH ORDINALITY AS iv(i, ord)
    ) t
    WHERE t.zone IS NOT NULL AND t.sec IS NOT NULL
) pf ON true
LEFT JOIN LATERAL (
    SELECT count(*) OVER () AS n, s.revision_number, s.updated_at, s.content_hash,
        -- SL6 (#532): the read-back matched when the row's ``state_hash`` was set (a NULL one is
        -- a write MOWL took that classbeat could not confirm, ADR 0007 section 8).
        (s.state_hash IS NOT NULL) AS verified,
        CASE WHEN s.operation_id IS NULL OR EXISTS (SELECT 1 FROM external_operations o
            WHERE o.owner_id = s.owner_id AND o.id = s.operation_id AND o.class_id = s.class_id)
        THEN s.id END AS updatable_id
    FROM saved_workouts s
    WHERE s.owner_id = c.owner_id AND s.class_id = c.id AND s.detached_at IS NULL
    ORDER BY s.updated_at DESC, s.id LIMIT 1) sv ON true
WHERE c.owner_id = :owner_id AND (CAST(:class_id AS uuid) IS NULL OR c.id = CAST(:class_id AS uuid))
    AND (CAST(:after_at AS timestamptz) IS NULL OR c.updated_at < CAST(:after_at AS timestamptz)
        OR (c.updated_at = CAST(:after_at AS timestamptz) AND c.id > CAST(:after_id AS uuid)))
ORDER BY c.updated_at DESC, c.id
LIMIT :limit""")  # noqa: S608 - constants only
