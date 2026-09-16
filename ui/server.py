"""Minimal stdlib HTTP server for the Table Studio demo.

Every mutating action is a table_transforms call. Session state lives in memory.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
import sys
import threading
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from table_transforms import (  # noqa: E402
    TableTransformError,
    apply_recipe,
    delete_columns,
    delete_rows,
    duplicate_rows,
    edit_cells,
    expand_merged_cells,
    get_column_paths,
    insert_columns,
    insert_rows,
    join_row_fragments,
    map_columns,
    merge_tables,
    move_rows,
    normalize_width,
    rebuild_hierarchy,
    remove_repeated_headers,
    set_headers,
    split_table,
    transfer_header_rows,
    validate_table,
)
from table_transforms.tree import table_width as lib_table_width  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = os.environ.get("TABLE_STUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("TABLE_STUDIO_PORT", "8765"))
HISTORY_LIMIT = 40

SINGLE_TABLE_OPS = {
    "edit_cells": edit_cells,
    "insert_rows": insert_rows,
    "delete_rows": delete_rows,
    "duplicate_rows": duplicate_rows,
    "move_rows": move_rows,
    "insert_columns": insert_columns,
    "delete_columns": delete_columns,
    "map_columns": map_columns,
    "set_headers": set_headers,
    "transfer_header_rows": transfer_header_rows,
    "rebuild_hierarchy": rebuild_hierarchy,
    "remove_repeated_headers": remove_repeated_headers,
    "join_row_fragments": join_row_fragments,
    "expand_merged_cells": expand_merged_cells,
    "normalize_width": normalize_width,
    "apply_recipe": apply_recipe,
}


def _basename(path: str) -> str:
    return str(path or "").replace("\\", "/").split("/")[-1]


def _count_body(rows: Any) -> int:
    n = 0

    def walk(items: Any) -> None:
        nonlocal n
        if not isinstance(items, list):
            return
        for row in items:
            n += 1
            if isinstance(row, dict):
                walk(row.get("children") or [])

    walk(rows)
    return n


def _target_of(record: dict) -> dict:
    if isinstance(record.get("target"), dict):
        return record["target"]
    if "headers" in record or "body" in record:
        return {k: record[k] for k in record if k in ("headers", "body") or k not in (
            "image", "image_path", "path", "source_folder", "category"
        )}
    return {"headers": [], "body": []}


def _call(fn, table: dict, args: dict) -> Any:
    accepted = {
        name
        for name, p in inspect.signature(fn).parameters.items()
        if name != "table" and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    if fn is merge_tables:
        accepted = {name for name in accepted if name != "tables"}
    kwargs = {k: copy.deepcopy(v) for k, v in args.items() if k in accepted}
    if fn is merge_tables:
        raise RuntimeError("merge_tables is dispatched separately")
    return fn(table, **kwargs)


class Session:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.doc_name = "Untitled"
        self.order: list[str] = []
        self.tables: dict[str, dict[str, Any]] = {}
        self.past: list[dict[str, Any]] = []
        self.future: list[dict[str, Any]] = []
        self.recipes: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return {
            "order": list(self.order),
            "tables": {
                tid: copy.deepcopy(rec["current"])
                for tid, rec in self.tables.items()
            },
            "meta": {
                tid: {
                    "name": rec["name"],
                    "image_key": rec["image_key"],
                    "source_folder": rec["source_folder"],
                    "category": copy.deepcopy(rec.get("category") or []),
                    "record": copy.deepcopy(rec.get("record") or {}),
                }
                for tid, rec in self.tables.items()
            },
            "log": copy.deepcopy(self.log),
        }

    def restore(self, snap: dict[str, Any]) -> None:
        self.order = list(snap["order"])
        meta = snap["meta"]
        currents = snap["tables"]
        keep = {}
        for tid in self.order:
            prev = self.tables.get(tid)
            info = meta[tid]
            keep[tid] = {
                "id": tid,
                "name": info["name"],
                "image_key": info["image_key"],
                "source_folder": info["source_folder"],
                "category": info.get("category") or [],
                "record": info.get("record") or {},
                "original": prev["original"] if prev else copy.deepcopy(currents[tid]),
                "current": copy.deepcopy(currents[tid]),
            }
        self.tables = keep
        self.log = copy.deepcopy(snap.get("log") or [])

    def push_history(self) -> None:
        self.past.append(self.snapshot())
        if len(self.past) > HISTORY_LIMIT:
            self.past = self.past[-HISTORY_LIMIT:]
        self.future.clear()

    def load(self, records: list[dict], doc_name: str = "labels.jsonl") -> None:
        self.doc_name = doc_name or "labels.jsonl"
        self.order = []
        self.tables = {}
        self.past = []
        self.future = []
        self.log = []
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                continue
            image = rec.get("image") or rec.get("image_path") or rec.get("path") or ""
            key = _basename(image) or f"table_{i:04d}.png"
            tid = uuid.uuid4().hex[:10]
            target = copy.deepcopy(_target_of(rec))
            if not isinstance(target.get("headers"), list):
                target["headers"] = []
            if not isinstance(target.get("body"), list):
                target["body"] = []
            extra = {
                k: v
                for k, v in rec.items()
                if k not in ("target", "headers", "body")
            }
            self.tables[tid] = {
                "id": tid,
                "name": key,
                "image_key": key,
                "source_folder": rec.get("source_folder") or "",
                "category": list(rec.get("category") or []),
                "record": extra,
                "original": copy.deepcopy(target),
                "current": target,
            }
            self.order.append(tid)

    def table_summary(self, tid: str) -> dict[str, Any]:
        rec = self.tables[tid]
        table = rec["current"]
        try:
            issues = validate_table(table)
        except TableTransformError as e:
            issues = [{"code": "malformed_table", "message": str(e)}]
        try:
            paths = get_column_paths(table)
        except TableTransformError:
            paths = []
        n_err = sum(1 for x in issues if x.get("code") in ("malformed_row", "malformed_table", "invalid_type"))
        return {
            "id": tid,
            "name": rec["name"],
            "image_key": rec["image_key"],
            "source_folder": rec["source_folder"],
            "category": rec["category"],
            "n_header_rows": len(table.get("headers") or []),
            "n_body_rows": _count_body(table.get("body") or []),
            "n_cols": lib_table_width(table),
            "column_paths": paths,
            "issue_count": len(issues),
            "error_count": n_err,
            "issues": issues[:30],
        }

    def state(self, active_id: str | None = None) -> dict[str, Any]:
        summaries = [self.table_summary(tid) for tid in self.order]
        active = active_id if active_id in self.tables else (self.order[0] if self.order else None)
        current = None
        original = None
        if active:
            current = copy.deepcopy(self.tables[active]["current"])
            original = copy.deepcopy(self.tables[active]["original"])
        folders: dict[str, int] = {}
        for s in summaries:
            folders[s["source_folder"] or "Tables"] = folders.get(s["source_folder"] or "Tables", 0) + 1
        return {
            "doc_name": self.doc_name,
            "tables": summaries,
            "active_id": active,
            "active": current,
            "original": original,
            "folders": folders,
            "can_undo": bool(self.past),
            "can_redo": bool(self.future),
            "recipes": copy.deepcopy(self.recipes),
            "log": copy.deepcopy(self.log[-30:]),
        }


SESSION = Session()


def _filter_args(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    return {k: v for k, v in args.items() if v is not None}


def _run_single(op: str, table: dict, args: dict) -> dict:
    fn = SINGLE_TABLE_OPS.get(op)
    if fn is None:
        raise TableTransformError(f"unknown operation {op!r}")
    return _call(fn, table, args)


def preview_or_apply(payload: dict, commit: bool) -> dict[str, Any]:
    op = payload.get("op")
    if not isinstance(op, str) or not op:
        raise TableTransformError("missing op")
    args = _filter_args(payload.get("args") or {})
    table_ids = payload.get("table_ids") or []
    if not isinstance(table_ids, list) or not table_ids:
        raise TableTransformError("table_ids must be a non-empty list")
    for tid in table_ids:
        if tid not in SESSION.tables:
            raise TableTransformError(f"unknown table {tid}")

    label = payload.get("label") or op.replace("_", " ")

    if op == "get_column_paths":
        tid = table_ids[0]
        levels = args.get("levels")
        return {
            "ok": True,
            "op": op,
            "column_paths": get_column_paths(SESSION.tables[tid]["current"], levels=levels),
        }
    if op == "validate_table":
        tid = table_ids[0]
        return {"ok": True, "op": op, "issues": validate_table(SESSION.tables[tid]["current"])}

    proposed: dict[str, dict] = {}
    new_tables: list[dict[str, Any]] = []
    remove_ids: list[str] = []
    preview_table = None

    if op == "merge_tables":
        if len(table_ids) < 2:
            raise TableTransformError("merge_tables requires at least two tables")
        tables = [SESSION.tables[tid]["current"] for tid in table_ids]
        merged = merge_tables(
            tables,
            column_maps=args.get("column_maps"),
            headers=args.get("headers", "first"),
            attachments=args.get("attachments"),
        )
        keep = table_ids[0]
        proposed[keep] = merged
        remove_ids = table_ids[1:]
        preview_table = merged
    elif op == "split_table":
        if len(table_ids) != 1:
            raise TableTransformError("split_table applies to one table")
        tid = table_ids[0]
        pieces = split_table(
            SESSION.tables[tid]["current"],
            boundaries=args.get("boundaries") or [],
            headers=args.get("headers", "inherit"),
            subtree=args.get("subtree", "reject"),
        )
        proposed[tid] = pieces[0]
        preview_table = pieces[0]
        src = SESSION.tables[tid]
        for i, piece in enumerate(pieces[1:], start=2):
            new_tables.append(
                {
                    "name": f"{src['name']} ({i})",
                    "image_key": src["image_key"],
                    "source_folder": src["source_folder"],
                    "category": list(src.get("category") or []),
                    "record": copy.deepcopy(src.get("record") or {}),
                    "original": copy.deepcopy(piece),
                    "current": copy.deepcopy(piece),
                    "after_id": tid,
                }
            )
    else:
        if op not in SINGLE_TABLE_OPS:
            raise TableTransformError(f"unknown operation {op!r}")
        for tid in table_ids:
            proposed[tid] = _run_single(op, SESSION.tables[tid]["current"], args)
        preview_table = proposed[table_ids[0]]

    issues = validate_table(preview_table) if isinstance(preview_table, dict) else []
    column_paths = get_column_paths(preview_table) if isinstance(preview_table, dict) else []

    if not commit:
        return {
            "ok": True,
            "preview": True,
            "op": op,
            "label": label,
            "table_ids": table_ids,
            "results": {tid: proposed[tid] for tid in proposed},
            "new_tables": [
                {"name": t["name"], "table": t["current"]} for t in new_tables
            ],
            "remove_ids": remove_ids,
            "preview_table": preview_table,
            "issues": issues,
            "column_paths": column_paths,
            "args": copy.deepcopy(args),
        }

    SESSION.push_history()
    for tid, table in proposed.items():
        SESSION.tables[tid]["current"] = copy.deepcopy(table)
    created_ids = []
    cursor = None
    for extra in new_tables:
        nid = uuid.uuid4().hex[:10]
        after = extra.pop("after_id")
        extra["id"] = nid
        SESSION.tables[nid] = extra
        idx = SESSION.order.index(cursor or after) + 1
        SESSION.order.insert(idx, nid)
        created_ids.append(nid)
        cursor = nid
    for rid in remove_ids:
        if rid in SESSION.order:
            SESSION.order.remove(rid)
        SESSION.tables.pop(rid, None)
    SESSION.log.append(
        {
            "op": op,
            "label": label,
            "table_ids": list(table_ids),
            "args": copy.deepcopy(args),
        }
    )
    if op not in ("merge_tables", "split_table") and op in SINGLE_TABLE_OPS:
        pass
    return {
        "ok": True,
        "preview": False,
        "op": op,
        "label": label,
        "created_ids": created_ids,
        "removed_ids": remove_ids,
        "issues": issues,
        "column_paths": column_paths,
        "state": SESSION.state(table_ids[0] if table_ids[0] in SESSION.tables else None),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            val = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise TableTransformError(f"invalid JSON: {e}") from e
        if not isinstance(val, dict):
            raise TableTransformError("request body must be an object")
        return val

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            qs = parse_qs(parsed.query)
            active = (qs.get("id") or [None])[0]
            with SESSION.lock:
                self._json(200, SESSION.state(active))
            return
        if path == "/api/export.jsonl":
            with SESSION.lock:
                lines = []
                for tid in SESSION.order:
                    rec = SESSION.tables[tid]
                    out = dict(rec.get("record") or {})
                    out["image"] = out.get("image") or f"images/{rec['image_key']}"
                    out["target"] = rec["current"]
                    if rec.get("source_folder"):
                        out["source_folder"] = rec["source_folder"]
                    if rec.get("category"):
                        out["category"] = rec["category"]
                    lines.append(json.dumps(out, ensure_ascii=False))
                body = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{SESSION.doc_name.rsplit(".", 1)[0]}_corrected.jsonl"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._read_json()
            with SESSION.lock:
                if path == "/api/load":
                    records = payload.get("records")
                    if records is None and isinstance(payload.get("jsonl"), str):
                        records = []
                        for i, line in enumerate(payload["jsonl"].splitlines(), start=1):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError as e:
                                raise TableTransformError(f"JSONL line {i}: {e}") from e
                    if not isinstance(records, list) or not records:
                        raise TableTransformError("load requires a non-empty records list or jsonl string")
                    SESSION.load(records, doc_name=str(payload.get("doc_name") or "labels.jsonl"))
                    self._json(200, {"ok": True, "state": SESSION.state()})
                    return
                if path == "/api/preview":
                    self._json(200, preview_or_apply(payload, commit=False))
                    return
                if path == "/api/apply":
                    self._json(200, preview_or_apply(payload, commit=True))
                    return
                if path == "/api/undo":
                    if not SESSION.past:
                        raise TableTransformError("nothing to undo")
                    SESSION.future.append(SESSION.snapshot())
                    SESSION.restore(SESSION.past.pop())
                    self._json(200, {"ok": True, "state": SESSION.state()})
                    return
                if path == "/api/redo":
                    if not SESSION.future:
                        raise TableTransformError("nothing to redo")
                    SESSION.past.append(SESSION.snapshot())
                    SESSION.restore(SESSION.future.pop())
                    self._json(200, {"ok": True, "state": SESSION.state()})
                    return
                if path == "/api/recipes":
                    name = str(payload.get("name") or "").strip()
                    steps = payload.get("steps")
                    if not name:
                        raise TableTransformError("recipe name is required")
                    if not isinstance(steps, list) or not steps:
                        raise TableTransformError("recipe steps must be a non-empty list")
                    recipe = {"id": uuid.uuid4().hex[:8], "name": name, "steps": copy.deepcopy(steps)}
                    SESSION.recipes.append(recipe)
                    self._json(200, {"ok": True, "recipe": recipe, "recipes": copy.deepcopy(SESSION.recipes)})
                    return
            self._json(404, {"ok": False, "error": f"unknown endpoint {path}"})
        except TableTransformError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(500, {"ok": False, "error": f"server error: {e}"})


def main() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Table Studio  {url}", flush=True)
    print("Upload a labels.jsonl and table images to begin.", flush=True)
    if os.environ.get("TABLE_STUDIO_NO_BROWSER") != "1":
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()
