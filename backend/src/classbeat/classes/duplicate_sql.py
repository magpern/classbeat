"""The SQL of ``classes.duplicate`` (ADR 0007 amendment of 2026-11-03, part D; #547): the source
draft and its overlay, the replay by key, the new class and revision, the copied corrections and
the adoption of the copy's revision. Every statement names ``owner_id``."""

from sqlalchemy import text

SOURCE = text(
    "SELECT r.id, r.number, r.content, r.content_hash, r.edit_overlay, r.music, r.provenance, "
    "c.brief, c.music_source FROM classes c JOIN class_revisions r ON r.owner_id = c.owner_id "
    "AND r.class_id = c.id AND r.id = :revision_id AND r.kind = 'draft' "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
# The user's own duplicate made with this key, if any (the replay of D item 7).
BY_KEY = text(
    "SELECT class_id, id, provenance -> 'duplicated_from' ->> 'request_hash' AS request_hash "
    "FROM class_revisions WHERE owner_id = :owner_id AND kind = 'draft' "
    "AND provenance ->> 'origin' = 'duplicate' "
    "AND provenance -> 'duplicated_from' ->> 'idempotency_key' = :key LIMIT 1"
)
NEW_CLASS = text(
    "INSERT INTO classes (id, owner_id, title, brief, music_source) "
    "VALUES (:id, :owner_id, :title, CAST(:brief AS jsonb), CAST(:music_source AS jsonb))"
)
NEW_REVISION = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id, parent_id) VALUES (:id, :owner_id, :class_id, 1, 'draft', "
    "CAST(:content AS jsonb), :content_hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
    "NULL, NULL)"
)
# DU3: the source class's corrections for the chosen revision's fingerprint, re-keyed to the copy.
# Both sides name the caller's ``owner_id`` (and row security holds them to it too); ``id``,
# ``created_at`` and ``updated_at`` are the table's defaults.
COPY_CORRECTIONS = text(
    "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
    "SELECT :owner_id, :new_class_id, fingerprint, song, beats, bpm FROM beat_one_corrections "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND fingerprint = :fingerprint "
    "ORDER BY song LIMIT :cap"
)
ADOPT = text(
    "UPDATE classes SET current_revision_id = :revision_id "
    "WHERE owner_id = :owner_id AND id = :class_id AND current_revision_id IS NULL"
)
