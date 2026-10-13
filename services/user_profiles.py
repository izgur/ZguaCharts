"""Versioned account documents with a localStorage snapshot projection.

The caller owns the transaction. BEGIN IMMEDIATE is also supported by the
PostgreSQL adapter, so migration and compare-and-swap share one writer lock.
"""
from __future__ import annotations

import json
import re
import time


FORMAT = "user-documents-v1"
KINDS = {
    "tvk-dashboard-state": "workspace",
    "zgua-ui-preferences": "preferences",
    "zgua-default-chart-layout-v2": "preferences",
    "zgua-manual-backtest-comparisons": "comparisons",
    "zgua-good-people-presets": "presets",
    "zgua-layouts-v1": "layouts",
    "zgua-indicator-favourites-v1": "favourites",
    "zgua-custom-indicators-v1": "indicators",
    "zgua-custom-tactics-v1": "tactics",
}


class ProfileConflict(Exception):
    def __init__(self, revision):
        self.revision = revision
        super().__init__("Profile changed in another tab. Reload before saving; local changes are still on this device.")


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def document_kind(key):
    if key.startswith("zgua-drawings-v1:"):
        return "drawings"
    if key.startswith("zgua-drawing-controls-v1:"):
        return "drawing_preferences"
    return KINDS.get(key, "legacy")


def normalize(snapshot):
    """Split collections into records; retain source keys for backward import."""
    documents = {}
    for key, raw in snapshot.items():
        version = re.search(r"-v(\d+)(?::|$)", key)
        source = {"storage_key": key, "source_version": int(version[1]) if version else None}
        encoding, value = "value", json.loads(encode(raw)) if isinstance(raw, (dict, list)) else raw
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
                encoding = "json"
            except (ValueError, TypeError):
                encoding = "text"
        container = {**source, "encoding": encoding, "value": value}
        records, kind = None, None
        if key == "zgua-layouts-v1" and isinstance(value, dict):
            records, kind = value.get("items"), "layout"
        elif key == "zgua-ui-preferences" and isinstance(value, dict):
            # Legacy single watchlists and newer named lists coexist in imports.
            for field in ("watchlist", "watchAll", "watchSource", "watchTab", "watchAllSource", "watchListTab"):
                if field in value:
                    documents[("watchlists", field)] = encode({**source, "value": value.pop(field)})
                    container.setdefault("fields", {})[field] = ["watchlists", field]
            records, kind = value.get("watchLists"), "watchlist"
        elif key in ("zgua-custom-indicators-v1", "zgua-custom-tactics-v1"):
            records = value
            kind = "indicator" if key == "zgua-custom-indicators-v1" else "tactic"
        elif key.startswith("zgua-drawings-v1:"):
            records, kind = value, "drawing"
        if isinstance(records, list):
            references, seen = [], set()
            for index, record in enumerate(records):
                identity = record.get("id") if isinstance(record, dict) else None
                # Stable IDs preserve source history when records are reordered.
                identity = identity if isinstance(identity, (str, int)) and not isinstance(identity, bool) else None
                document_key = encode([key, identity]) if identity is not None else encode([key, None, index])
                if document_key in seen:
                    document_key = encode([key, identity, index])
                seen.add(document_key)
                references.append([kind, document_key])
                documents[(kind, document_key)] = encode({**source, "value": record})
            field = "items" if kind == "layout" else "watchLists" if kind == "watchlist" else None
            if field:
                value.pop(field)
            else:
                container["value"] = None
            container["collection"] = {"field": field, "records": references}
        documents[(document_kind(key), key)] = encode(container)
    return documents


def sync_documents(db, user_id, snapshot):
    documents = normalize(snapshot)
    existing = {(row["kind"], row["document_key"]): row for row in db.execute(
        "SELECT kind,document_key,body,revision FROM user_documents WHERE user_id=?", (user_id,)
    ).fetchall()}
    now = time.time()
    for (kind, key), body in documents.items():
        previous = existing.get((kind, key))
        if previous and previous["body"] == body:
            continue
        revision = previous["revision"] + 1 if previous else 1
        if kind in ("indicator", "tactic"):
            history = db.execute(
                "SELECT MAX(revision) AS revision FROM user_document_versions WHERE user_id=? AND kind=? AND document_key=?",
                (user_id, kind, key),
            ).fetchone()
            revision = max(revision, (history["revision"] or 0) + 1)
            db.execute(
                "INSERT INTO user_document_versions(user_id,kind,document_key,revision,body,created) VALUES (?,?,?,?,?,?)",
                (user_id, kind, key, revision, body, now),
            )
        if previous:
            db.execute("UPDATE user_documents SET body=?,revision=?,updated=? WHERE user_id=? AND kind=? AND document_key=?",
                       (body, revision, now, user_id, kind, key))
        else:
            db.execute("INSERT INTO user_documents(user_id,kind,document_key,body,revision,updated) VALUES (?,?,?,?,?,?)",
                       (user_id, kind, key, body, revision, now))
    for kind, key in existing.keys() - documents.keys():
        db.execute("DELETE FROM user_documents WHERE user_id=? AND kind=? AND document_key=?", (user_id, kind, key))


def load_profile(db, user_id):
    if not getattr(db, "in_transaction", False):
        db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT body,revision FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return None, 0
    body = json.loads(row["body"])
    if not (isinstance(body, dict) and body.get("format") == FORMAT and body.get("normalized") is True):
        sync_documents(db, user_id, body)
        db.execute("UPDATE profiles SET body=? WHERE user_id=?", (encode({"format": FORMAT, "normalized": True}), user_id))
        return body, row["revision"]
    snapshot = {}
    documents = {(document["kind"], document["document_key"]): json.loads(document["body"])
                 for document in db.execute("SELECT kind,document_key,body FROM user_documents WHERE user_id=?", (user_id,)).fetchall()}
    for document in documents.values():
        if "encoding" not in document:
            continue
        value = document["value"]
        for field, reference in document.get("fields", {}).items():
            value[field] = documents[tuple(reference)]["value"]
        if "collection" in document:
            collection = document["collection"]
            records = [documents[tuple(reference)]["value"] for reference in collection["records"]]
            if collection["field"]:
                value[collection["field"]] = records
            else:
                value = records
        snapshot[document["storage_key"]] = json.dumps(value, ensure_ascii=True) if document["encoding"] == "json" else value
    return snapshot, row["revision"]


def save_snapshot(db, user_id, body, expected_revision):
    """Save a raw snapshot in the caller's transaction and return its new revision.

    Pass revision 0 for a first import. ProfileConflict rolls back when allowed
    to propagate through storage.database(); never commit a caught conflict.
    """
    if not isinstance(body, dict) or not all(isinstance(key, str) for key in body):
        raise ValueError("Invalid profile.")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid profile revision.")
    encode(body)
    if not getattr(db, "in_transaction", False):
        db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT revision FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        if expected_revision != 0:
            raise ProfileConflict(0)
        db.execute("INSERT INTO profiles(user_id,body,revision) VALUES (?,?,0)", (user_id, "{}"))
    changed = db.execute(
        "UPDATE profiles SET body=?,revision=revision+1 WHERE user_id=? AND revision=?",
        (encode({"format": FORMAT, "normalized": True}), user_id, expected_revision),
    )
    if changed.rowcount != 1:
        revision = db.execute("SELECT revision FROM profiles WHERE user_id=?", (user_id,)).fetchone()["revision"]
        raise ProfileConflict(revision)
    sync_documents(db, user_id, body)
    return expected_revision + 1
