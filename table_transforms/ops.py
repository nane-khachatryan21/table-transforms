"""Deterministic transformations on hierarchical table JSON."""

from __future__ import annotations

import copy
from typing import Any, Iterable, Sequence

from .errors import TableTransformError
from .tree import (
    as_index,
    as_optional_path,
    as_path,
    assert_paths_exist,
    clone_row,
    coerce_inserted_row,
    deepcopy_table,
    format_path,
    get_row,
    header_width,
    insert_index,
    int_keyed_dict,
    map_body_columns,
    map_headers,
    parent_children,
    path_is_prefix,
    preorder,
    reconstruct_body,
    reject_overlapping_paths,
    remap_path_after_deletes,
    require_body,
    require_headers,
    require_string_cell,
    resolve_column_mapping,
    row_data_of,
    table_width,
)

_MISSING = object()


def _region_cells(table: dict, region: str, path: Sequence[int]) -> list[Any]:
    if region == "header":
        headers = require_headers(table)
        if len(path) != 1:
            raise TableTransformError("header path must be a single row index")
        row_i = path[0]
        if row_i < 0 or row_i >= len(headers):
            raise TableTransformError(f"header row {row_i} does not exist")
        row = headers[row_i]
        if not isinstance(row, list):
            raise TableTransformError(f"header row {row_i} is not a list")
        return row
    if region == "body":
        row = get_row(require_body(table), path)
        return row_data_of(row, path)
    raise TableTransformError("region must be 'header' or 'body'")


def _require_choice(value: Any, allowed: Iterable[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(repr(v) for v in allowed)
        raise TableTransformError(f"{name} must be one of: {choices}")
    return value


def _delete_body_paths(body: list[Any], paths: Sequence[Sequence[int]]) -> list[Any]:
    doomed = {tuple(p) for p in paths}

    def rec(rows: list[Any], prefix: tuple[int, ...]) -> list[Any]:
        out = []
        for i, row in enumerate(rows):
            p = prefix + (i,)
            if p in doomed or any(path_is_prefix(d, p) for d in doomed):
                continue
            node = copy.copy(row) if isinstance(row, dict) else row
            if isinstance(node, dict):
                kids = node.get("children")
                if isinstance(kids, list):
                    node = dict(node)
                    node["children"] = rec(kids, p)
            out.append(node)
        return out

    return rec(body, ())


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def edit_cells(table: dict, changes: Sequence[dict]) -> dict:
    """Apply specified value changes to header or body cells."""
    out = deepcopy_table(table)
    if not isinstance(changes, (list, tuple)) or not changes:
        raise TableTransformError("changes must be a non-empty list")
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise TableTransformError(f"change {i} must be an object")
        region = _require_choice(change.get("region"), ("header", "body"), "region")
        if region == "header":
            if "path" in change:
                path = as_path(change["path"], "path")
            elif "row" in change:
                path = [as_index(change["row"], "row")]
            else:
                raise TableTransformError(f"change {i} needs path or row")
        else:
            path = as_path(change.get("path"), "path")
        col = as_index(change.get("column"), "column")
        if col < 0:
            raise TableTransformError(f"change {i} column must be >= 0")
        if "value" not in change:
            raise TableTransformError(f"change {i} is missing value")
        value = require_string_cell(change["value"], f"change {i}")
        cells = _region_cells(out, region, path)
        if col > len(cells):
            raise TableTransformError(
                f"change {i} column {col} skips cells on a row of length {len(cells)}; "
                "pad with normalize_width or edit in order"
            )
        if col == len(cells):
            cells.append(value)
        else:
            cells[col] = value
    return out


def insert_rows(
    table: dict,
    rows: Sequence[dict],
    parent: Sequence[int] | None = None,
    index: int | None = None,
) -> dict:
    """Insert supplied rows at a specified position, including under a parent."""
    out = deepcopy_table(table)
    if not isinstance(rows, (list, tuple)) or not rows:
        raise TableTransformError("rows must be a non-empty list")
    inserted = [coerce_inserted_row(r) for r in rows]
    parent_path = as_optional_path(parent, "parent")
    dest = parent_children(out, parent_path)
    at = insert_index(len(dest), index)
    dest[at:at] = inserted
    return out


def delete_rows(table: dict, paths: Sequence[Sequence[int]]) -> dict:
    """Remove selected rows and their subtrees."""
    out = deepcopy_table(table)
    body = require_body(out)
    resolved = assert_paths_exist(body, paths)
    out["body"] = _delete_body_paths(body, resolved)
    return out


def duplicate_rows(table: dict, paths: Sequence[Sequence[int]]) -> dict:
    """Copy selected rows with their subtrees, inserting each copy after the original."""
    out = deepcopy_table(table)
    body = require_body(out)
    resolved = assert_paths_exist(body, paths)
    selected = {tuple(p) for p in resolved}

    def rec(rows: list[Any], prefix: tuple[int, ...]) -> list[Any]:
        new: list[Any] = []
        for i, row in enumerate(rows):
            p = prefix + (i,)
            node = copy.copy(row) if isinstance(row, dict) else row
            if isinstance(node, dict):
                node = dict(node)
                kids = node.get("children")
                if isinstance(kids, list):
                    node["children"] = rec(kids, p)
            new.append(node)
            if p in selected:
                new.append(clone_row(row))
        return new

    out["body"] = rec(body, ())
    return out


def move_rows(
    table: dict,
    paths: Sequence[Sequence[int]],
    parent: Sequence[int] | None = None,
    index: int | None = None,
) -> dict:
    """Move rows with their subtrees to a parent and position (reorder / indent / outdent)."""
    src = deepcopy_table(table)
    body = require_body(src)
    resolved = assert_paths_exist(body, paths)
    reject_overlapping_paths(resolved, "move_rows")
    dest_parent = as_optional_path(parent, "parent")
    if dest_parent is not None:
        get_row(body, dest_parent)
        for path in resolved:
            if path_is_prefix(path, dest_parent):
                raise TableTransformError(
                    f"cannot move {format_path(path)} into its own subtree at {format_path(dest_parent)}"
                )

    moved = [clone_row(get_row(body, p)) for p in resolved]
    remaining = _delete_body_paths(body, resolved)
    src["body"] = remaining
    remapped = remap_path_after_deletes(dest_parent, resolved) if dest_parent is not None else None
    dest = parent_children(src, remapped)
    at = insert_index(len(dest), index)
    dest[at:at] = moved
    return src


def insert_columns(
    table: dict,
    index: int,
    count: int = 1,
    fill: str = "",
) -> dict:
    """Insert columns consistently across headers and all nested rows."""
    out = deepcopy_table(table)
    count = as_index(count, "count")
    if count < 1:
        raise TableTransformError("count must be >= 1")
    require_string_cell(fill, "fill")
    at = as_index(index, "index")
    if at < 0:
        raise TableTransformError("index must be >= 0")
    width = table_width(out)
    if at > width:
        raise TableTransformError(
            f"insert index {at} skips columns on a table of width {width}; "
            "use normalize_width first if trailing cells are missing"
        )

    def insert_into(cells: list[Any]) -> list[Any]:
        data = list(cells)
        if at > len(data):
            data.extend([""] * (at - len(data)))
        return data[:at] + [fill] * count + data[at:]

    headers = require_headers(out)
    for i, row in enumerate(headers):
        if not isinstance(row, list):
            raise TableTransformError(f"header row {i} is not a list")
        headers[i] = insert_into(row)

    def rec(rows: list[Any]) -> None:
        for path_row in rows:
            if not isinstance(path_row, dict):
                raise TableTransformError("body row must be an object")
            data = path_row.get("row_data")
            if not isinstance(data, list):
                raise TableTransformError("body row is missing a row_data list")
            path_row["row_data"] = insert_into(data)
            kids = path_row.get("children")
            if isinstance(kids, list):
                rec(kids)

    rec(require_body(out))
    return out


def delete_columns(table: dict, columns: Sequence[int]) -> dict:
    """Remove specified columns throughout the table."""
    out = deepcopy_table(table)
    if not isinstance(columns, (list, tuple)) or not columns:
        raise TableTransformError("columns must be a non-empty list")
    width = table_width(out)
    seen: set[int] = set()
    drop: list[int] = []
    for col in columns:
        col = as_index(col, "column")
        if col < 0 or col >= width:
            raise TableTransformError(f"column {col} is out of range for width {width}")
        if col in seen:
            raise TableTransformError(f"column {col} is listed more than once")
        seen.add(col)
        drop.append(col)
    drop_set = set(drop)

    def strip(cells: list[Any]) -> list[Any]:
        return [c for i, c in enumerate(cells) if i not in drop_set]

    headers = require_headers(out)
    for i, row in enumerate(headers):
        if not isinstance(row, list):
            raise TableTransformError(f"header row {i} is not a list")
        headers[i] = strip(row)

    def rec(rows: list[Any]) -> None:
        for row in rows:
            if not isinstance(row, dict):
                raise TableTransformError("body row must be an object")
            data = row.get("row_data")
            if not isinstance(data, list):
                raise TableTransformError("body row is missing a row_data list")
            row["row_data"] = strip(data)
            kids = row.get("children")
            if isinstance(kids, list):
                rec(kids)

    rec(require_body(out))
    return out


def map_columns(table: dict, mapping: Any, exclude: Sequence[int] | None = None) -> dict:
    """Apply an explicit source-to-target column mapping/order throughout the table.

    Unmapped columns are preserved in original order unless listed in exclude.
    ``mapping`` is either a list of source indices (output order prefix) or a
    dict of source index -> target index.
    """
    out = deepcopy_table(table)
    n_cols = table_width(out)
    sources = resolve_column_mapping(n_cols, mapping, exclude)
    out["headers"] = map_headers(require_headers(out), sources)
    out["body"] = map_body_columns(require_body(out), sources)
    return out


def set_headers(
    table: dict,
    headers: Sequence[Sequence[str]] | None = None,
    edits: Sequence[dict] | None = None,
) -> dict:
    """Replace or edit the header matrix, preserving multiple header levels."""
    out = deepcopy_table(table)
    if headers is None and not edits:
        raise TableTransformError("set_headers requires headers and/or edits")
    if headers is not None:
        if not isinstance(headers, (list, tuple)):
            raise TableTransformError("headers must be a list of rows")
        matrix: list[list[str]] = []
        for i, row in enumerate(headers):
            if not isinstance(row, (list, tuple)):
                raise TableTransformError(f"header row {i} must be a list")
            cells: list[str] = []
            for j, cell in enumerate(row):
                if not isinstance(cell, str):
                    raise TableTransformError(f"header [{i}, {j}] must be a string")
                cells.append(cell)
            matrix.append(cells)
        out["headers"] = matrix
    if edits:
        if not isinstance(edits, (list, tuple)):
            raise TableTransformError("edits must be a list")
        for i, change in enumerate(edits):
            if not isinstance(change, dict):
                raise TableTransformError(f"edit {i} must be an object")
            row_i = as_index(change.get("row"), "row")
            col = as_index(change.get("column"), "column")
            if "value" not in change:
                raise TableTransformError(f"edit {i} is missing value")
            value = require_string_cell(change["value"], f"edit {i}")
            hdrs = require_headers(out)
            if row_i < 0 or row_i >= len(hdrs):
                raise TableTransformError(f"header row {row_i} does not exist")
            row = hdrs[row_i]
            if not isinstance(row, list):
                raise TableTransformError(f"header row {row_i} is not a list")
            if col < 0 or col > len(row):
                raise TableTransformError(
                    f"edit {i} column {col} skips cells on a header row of length {len(row)}"
                )
            if col == len(row):
                row.append(value)
            else:
                row[col] = value
    return out


def get_column_paths(
    table: dict,
    levels: Sequence[int] | None = None,
    separator: str = " / ",
) -> list[str]:
    """Combine selected header levels into unambiguous column paths."""
    if not isinstance(separator, str):
        raise TableTransformError("separator must be a string")
    headers = table.get("headers")
    if not isinstance(headers, list) or not headers:
        return []
    n_rows = len(headers)
    if levels is None:
        level_idx = list(range(n_rows))
    else:
        if not isinstance(levels, (list, tuple)) or not levels:
            raise TableTransformError("levels must be a non-empty list of header row indices")
        level_idx = [as_index(i, "level") for i in levels]
        for i in level_idx:
            if i < 0 or i >= n_rows:
                raise TableTransformError(f"header level {i} does not exist")
    width = header_width(headers)
    paths: list[str] = []
    for col in range(width):
        parts: list[str] = []
        for level in level_idx:
            row = headers[level]
            if not isinstance(row, list) or col >= len(row):
                continue
            val = row[col]
            if not isinstance(val, str) or val == "":
                continue
            if not parts or parts[-1] != val:
                parts.append(val)
        paths.append(separator.join(parts) if parts else f"Column {col}")
    seen: dict[str, list[int]] = {}
    for i, name in enumerate(paths):
        seen.setdefault(name, []).append(i)
    for name, cols in seen.items():
        if len(cols) > 1:
            for col in cols:
                paths[col] = f"{name} [{col}]"
    return paths


def transfer_header_rows(
    table: dict,
    direction: str,
    mode: str,
    rows: Sequence[Any],
    parent: Sequence[int] | None = None,
    index: int | None = None,
    at: int | None = None,
    on_children: str | None = None,
) -> dict:
    """Copy or move rows between headers and body. ``mode`` must be ``copy`` or ``move``.

    Body rows with children: ``copy`` copies only the selected row unless
    ``on_children='flatten'``. ``move`` requires ``on_children='flatten'``
    (descendants become extra header rows) or ``'promote'`` (children stay in
    the body).
    """
    out = deepcopy_table(table)
    direction = _require_choice(direction, ("to_body", "to_headers"), "direction")
    mode = _require_choice(mode, ("copy", "move"), "mode")
    if not isinstance(rows, (list, tuple)) or not rows:
        raise TableTransformError("rows must be a non-empty list")

    if direction == "to_body":
        header_idx = [as_index(i, "rows") for i in rows]
        headers = require_headers(out)
        seen: set[int] = set()
        extracted: list[list[Any]] = []
        for i in header_idx:
            if i < 0 or i >= len(headers):
                raise TableTransformError(f"header row {i} does not exist")
            if i in seen:
                raise TableTransformError(f"header row {i} is selected more than once")
            seen.add(i)
            row = headers[i]
            if not isinstance(row, list):
                raise TableTransformError(f"header row {i} is not a list")
            extracted.append(list(row))
        new_rows = [{"row_data": cells, "children": []} for cells in extracted]
        dest = parent_children(out, as_optional_path(parent, "parent"))
        at_body = insert_index(len(dest), index)
        dest[at_body:at_body] = new_rows
        if mode == "move":
            out["headers"] = [r for i, r in enumerate(headers) if i not in seen]
        return out

    body = require_body(out)
    body_paths = assert_paths_exist(body, rows)
    reject_overlapping_paths(body_paths, "transfer_header_rows")
    header_rows: list[list[Any]] = []
    drop_paths: list[list[int]] = []
    promote_only: list[list[int]] = []
    for path in body_paths:
        row = get_row(body, path)
        data = list(row_data_of(row, path))
        kids = row.get("children") or []
        has_kids = isinstance(kids, list) and len(kids) > 0
        if has_kids and mode == "move":
            handling = (
                _require_choice(on_children, ("flatten", "promote"), "on_children")
                if on_children is not None
                else None
            )
            if handling is None:
                raise TableTransformError(
                    f"row {format_path(path)} has children; pass on_children='flatten' or 'promote'"
                )
            if handling == "flatten":
                for p, _depth, node in preorder([row]):
                    header_rows.append(list(row_data_of(node)))
                drop_paths.append(path)
            else:
                header_rows.append(data)
                promote_only.append(path)
        elif has_kids and on_children == "flatten":
            for p, _depth, node in preorder([row]):
                header_rows.append(list(row_data_of(node)))
        else:
            if has_kids and on_children not in (None, "row"):
                _require_choice(on_children, ("flatten", "row"), "on_children")
            header_rows.append(data)
            if mode == "move":
                drop_paths.append(path)

    headers = require_headers(out)
    insert_at = insert_index(len(headers), at)
    out["headers"] = list(headers[:insert_at]) + header_rows + list(headers[insert_at:])
    if promote_only or drop_paths:
        promote_set = {tuple(p) for p in promote_only}
        drop_set = {tuple(p) for p in drop_paths}

        def rec(rows: list[Any], prefix: tuple[int, ...]) -> list[Any]:
            new: list[Any] = []
            for i, row in enumerate(rows):
                p = prefix + (i,)
                if p in drop_set:
                    continue
                if p in promote_set:
                    kids = row.get("children") if isinstance(row, dict) else []
                    if isinstance(kids, list):
                        new.extend(rec(kids, p))
                    continue
                node = dict(row) if isinstance(row, dict) else row
                if isinstance(node, dict) and isinstance(node.get("children"), list):
                    node["children"] = rec(node["children"], p)
                new.append(node)
            return new

        out["body"] = rec(require_body(out), ())
    return out


def rebuild_hierarchy(
    table: dict,
    start: Sequence[int],
    end: Sequence[int] | None = None,
    depths: Sequence[int] | None = None,
    parents: Sequence[int | None] | None = None,
) -> dict:
    """Rebuild a selected body range from depths or parent assignments, preserving text and order."""
    if depths is None and parents is None:
        raise TableTransformError("rebuild_hierarchy requires depths or parents")
    if depths is not None and parents is not None:
        raise TableTransformError("provide depths or parents, not both")
    out = deepcopy_table(table)
    body = require_body(out)
    start_path = as_path(start, "start")
    flat = preorder(body)
    if not flat:
        raise TableTransformError("body is empty")
    index_of = {tuple(p): i for i, (p, _d, _r) in enumerate(flat)}
    if tuple(start_path) not in index_of:
        raise TableTransformError(f"start path {format_path(start_path)} does not exist")
    start_i = index_of[tuple(start_path)]
    if end is None:
        start_depth = flat[start_i][1]
        end_i = start_i + 1
        while end_i < len(flat) and flat[end_i][1] > start_depth:
            end_i += 1
        end_i -= 1
    else:
        end_path = as_path(end, "end")
        if tuple(end_path) not in index_of:
            raise TableTransformError(f"end path {format_path(end_path)} does not exist")
        end_i = index_of[tuple(end_path)]
    if end_i < start_i:
        raise TableTransformError("end path precedes start path")
    span = end_i - start_i + 1
    new_depths: list[int]
    if depths is not None:
        if not isinstance(depths, (list, tuple)) or len(depths) != span:
            raise TableTransformError(f"depths must have {span} entries for the selected range")
        new_depths = [as_index(d, "depth") for d in depths]
    else:
        if not isinstance(parents, (list, tuple)) or len(parents) != span:
            raise TableTransformError(f"parents must have {span} entries for the selected range")
        rel: list[int] = []
        for i, parent in enumerate(parents):
            if parent is None:
                rel.append(0)
                continue
            p = as_index(parent, "parent")
            if p < 0 or p >= i:
                raise TableTransformError(f"parents[{i}] must be None or an earlier index in the range")
            rel.append(rel[p] + 1)
        if rel and rel[0] != 0:
            raise TableTransformError("the first row in the range cannot have a parent in the range")
        base = flat[start_i][1]
        new_depths = [base + d for d in rel]

    merged: list[tuple[int, dict]] = []
    for i, (_path, depth, row) in enumerate(flat):
        if start_i <= i <= end_i:
            merged.append((new_depths[i - start_i], row))
        else:
            merged.append((depth, row))
    out["body"] = reconstruct_body(merged)
    return out


def merge_tables(
    tables: Sequence[dict],
    column_maps: Sequence[Any] | None = None,
    headers: Any = "first",
    attachments: dict | None = None,
) -> dict:
    """Combine ordered tables with explicit column mappings, header handling, and attachments."""
    if not isinstance(tables, (list, tuple)) or len(tables) < 2:
        raise TableTransformError("merge_tables requires at least two tables")
    copies = [deepcopy_table(t) for t in tables]
    if column_maps is None:
        maps: list[Any] = [None] * len(copies)
    else:
        if not isinstance(column_maps, (list, tuple)) or len(column_maps) != len(copies):
            raise TableTransformError("column_maps must have one mapping per table")
        maps = list(column_maps)

    aligned: list[dict] = []
    for t, mapping in zip(copies, maps):
        n_cols = table_width(t)
        sources = resolve_column_mapping(n_cols, mapping, exclude=None)
        aligned.append(
            {
                **{k: v for k, v in t.items() if k not in ("headers", "body")},
                "headers": map_headers(require_headers(t), sources),
                "body": map_body_columns(require_body(t), sources),
            }
        )

    if isinstance(headers, str):
        mode = _require_choice(headers, ("first", "concat", "none"), "headers")
        if mode == "first":
            header_matrix = copy.deepcopy(aligned[0]["headers"])
        elif mode == "none":
            header_matrix = []
        else:
            header_matrix = []
            for t in aligned:
                header_matrix.extend(copy.deepcopy(t["headers"]))
    elif isinstance(headers, (list, tuple)):
        header_matrix = []
        for i, row in enumerate(headers):
            if not isinstance(row, (list, tuple)):
                raise TableTransformError(f"header row {i} must be a list")
            cells = []
            for j, cell in enumerate(row):
                if not isinstance(cell, str):
                    raise TableTransformError(f"header [{i}, {j}] must be a string")
                cells.append(cell)
            header_matrix.append(cells)
    else:
        raise TableTransformError("headers must be 'first', 'concat', 'none', or an explicit matrix")

    attach = int_keyed_dict(attachments, "attachments") if attachments else {}
    for key in attach:
        if key < 1 or key >= len(aligned):
            raise TableTransformError(f"attachments key {key} must be a table index >= 1")

    result = {**aligned[0], "headers": header_matrix, "body": copy.deepcopy(aligned[0]["body"])}

    for t_index in range(1, len(aligned)):
        incoming = copy.deepcopy(aligned[t_index]["body"])
        spec = attach.get(t_index)
        if spec is None:
            result["body"].extend(incoming)
            continue
        if not isinstance(spec, dict):
            raise TableTransformError(f"attachments[{t_index}] must be an object")
        if "row_parents" not in spec and "parent" not in spec:
            raise TableTransformError(
                f"attachments[{t_index}] must include 'parent' or 'row_parents'"
            )
        _attach_rows(result, incoming, spec, t_index)
    return result


def _attach_rows(result: dict, incoming: list[Any], spec: dict, t_index: int) -> None:
    if "row_parents" in spec:
        row_parents = int_keyed_dict(spec["row_parents"], "row_parents")
        for i in row_parents:
            if i < 0 or i >= len(incoming):
                raise TableTransformError(f"attachments[{t_index}] row_parents index {i} is out of range")
        remaining: list[Any] = []
        for i, row in enumerate(incoming):
            if i not in row_parents:
                remaining.append(row)
                continue
            parent = row_parents[i]
            if parent is None:
                remaining.append(row)
                continue
            dest = parent_children(result, as_path(parent, "parent"))
            dest.append(row)
        result["body"].extend(remaining)
        return

    dest = parent_children(result, as_path(spec.get("parent"), "parent"))
    at = insert_index(len(dest), spec.get("index"))
    dest[at:at] = incoming


def remove_repeated_headers(
    table: dict,
    start: Sequence[int] | None = None,
    end: Sequence[int] | None = None,
    paths: Sequence[Sequence[int]] | None = None,
    header_rows: Sequence[int] | None = None,
    on_children: str | None = None,
) -> dict:
    """Remove explicitly selected or exactly matched header occurrences in a body range."""
    out = deepcopy_table(table)
    body = require_body(out)
    flat = preorder(body)
    index_of = {tuple(p): i for i, (p, _d, _r) in enumerate(flat)}
    lo, hi = 0, len(flat) - 1
    if start is not None:
        sp = as_path(start, "start")
        if tuple(sp) not in index_of:
            raise TableTransformError(f"start path {format_path(sp)} does not exist")
        lo = index_of[tuple(sp)]
    if end is not None:
        ep = as_path(end, "end")
        if tuple(ep) not in index_of:
            raise TableTransformError(f"end path {format_path(ep)} does not exist")
        hi = index_of[tuple(ep)]
    if lo > hi and flat:
        raise TableTransformError("end path precedes start path")

    def in_range(path: Sequence[int]) -> bool:
        i = index_of.get(tuple(path))
        return i is not None and lo <= i <= hi

    drop: list[list[int]] = []
    if paths is not None:
        if not isinstance(paths, (list, tuple)) or not paths:
            raise TableTransformError("paths must be a non-empty list")
        drop = assert_paths_exist(body, paths)
        for p in drop:
            if not in_range(p):
                raise TableTransformError(f"path {format_path(p)} is outside the specified body range")
    else:
        headers = require_headers(out)
        if header_rows is None:
            targets = [list(r) for r in headers if isinstance(r, list)]
        else:
            targets = []
            for i in header_rows:
                i = as_index(i, "header_rows")
                if i < 0 or i >= len(headers):
                    raise TableTransformError(f"header row {i} does not exist")
                row = headers[i]
                if not isinstance(row, list):
                    raise TableTransformError(f"header row {i} is not a list")
                targets.append(list(row))
        for path, _depth, row in flat:
            if not in_range(path):
                continue
            data = row.get("row_data")
            if isinstance(data, list) and any(data == h for h in targets):
                drop.append(path)

    handling = None
    for path in drop:
        row = get_row(body, path)
        kids = row.get("children") or []
        if isinstance(kids, list) and kids:
            if on_children is None:
                raise TableTransformError(
                    f"repeated header at {format_path(path)} has children; "
                    "pass on_children='promote' or 'drop'"
                )
            handling = _require_choice(on_children, ("promote", "drop"), "on_children")

    promote = handling == "promote"
    doomed = {tuple(p) for p in drop}

    def rec(rows: list[Any], prefix: tuple[int, ...]) -> list[Any]:
        new: list[Any] = []
        for i, row in enumerate(rows):
            p = prefix + (i,)
            if p in doomed:
                kids = row.get("children") if isinstance(row, dict) else []
                if promote and isinstance(kids, list):
                    new.extend(rec(kids, p))
                continue
            node = dict(row) if isinstance(row, dict) else row
            if isinstance(node, dict) and isinstance(node.get("children"), list):
                node["children"] = rec(node["children"], p)
            new.append(node)
        return new

    out["body"] = rec(body, ())
    return out


def split_table(
    table: dict,
    boundaries: Sequence[Sequence[int]],
    headers: Any = "inherit",
    subtree: str = "reject",
) -> list[dict]:
    """Split at specified row boundaries with explicit header inheritance.

    Each boundary is the first row of the next piece (split before that row).
    Cuts that separate a parent from descendants are rejected unless
    ``subtree='promote'``.
    """
    src = deepcopy_table(table)
    subtree = _require_choice(subtree, ("reject", "promote"), "subtree")
    if not isinstance(boundaries, (list, tuple)) or not boundaries:
        raise TableTransformError("boundaries must be a non-empty list of row paths")
    body = require_body(src)
    flat = preorder(body)
    index_of = {tuple(p): i for i, (p, _d, _r) in enumerate(flat)}
    cuts: list[int] = []
    seen: set[int] = set()
    for b in boundaries:
        path = as_path(b, "boundary")
        key = tuple(path)
        if key not in index_of:
            raise TableTransformError(f"boundary {format_path(path)} does not exist")
        idx = index_of[key]
        if idx in seen:
            raise TableTransformError(f"duplicate split boundary {format_path(path)}")
        if idx == 0:
            raise TableTransformError(f"boundary {format_path(path)} would create an empty first table")
        seen.add(idx)
        cuts.append(idx)
    cuts.sort()
    cuts.append(len(flat))

    ranges: list[tuple[int, int]] = []
    prev = 0
    for cut in cuts:
        if cut == prev:
            raise TableTransformError("split would create an empty table")
        ranges.append((prev, cut))
        prev = cut

    if subtree == "reject":
        for a, b in ranges:
            paths_in = {tuple(flat[i][0]) for i in range(a, b)}
            for i in range(a, b):
                path = flat[i][0]
                if len(path) > 1 and tuple(path[:-1]) not in paths_in:
                    raise TableTransformError(
                        f"split cuts through the subtree of {format_path(path[:-1])}; "
                        "pass subtree='promote' to allow it"
                    )

    pieces_flat = [[flat[i] for i in range(a, b)] for a, b in ranges]
    n = len(pieces_flat)
    if isinstance(headers, str):
        mode = _require_choice(headers, ("inherit", "first", "none"), "headers")
        header_sets: list[list[Any]]
        if mode == "inherit":
            header_sets = [copy.deepcopy(require_headers(src)) for _ in range(n)]
        elif mode == "first":
            header_sets = [copy.deepcopy(require_headers(src))] + [[] for _ in range(n - 1)]
        else:
            header_sets = [[] for _ in range(n)]
    elif isinstance(headers, (list, tuple)):
        if len(headers) != n:
            raise TableTransformError(f"headers must have one matrix per piece ({n})")
        header_sets = []
        for i, matrix in enumerate(headers):
            if not isinstance(matrix, (list, tuple)):
                raise TableTransformError(f"headers[{i}] must be a list of rows")
            piece: list[list[str]] = []
            for r, row in enumerate(matrix):
                if not isinstance(row, (list, tuple)):
                    raise TableTransformError(f"headers[{i}][{r}] must be a list")
                piece.append([require_string_cell(c, f"headers[{i}][{r}]") for c in row])
            header_sets.append(piece)
    else:
        raise TableTransformError("headers must be 'inherit', 'first', 'none', or a list of matrices")

    extras = {k: copy.deepcopy(v) for k, v in src.items() if k not in ("headers", "body")}
    result: list[dict] = []
    for piece, hdrs in zip(pieces_flat, header_sets):
        rebuilt = reconstruct_body([(depth, row) for _p, depth, row in piece])
        result.append({**copy.deepcopy(extras), "headers": hdrs, "body": rebuilt})
    return result


def join_row_fragments(
    table: dict,
    groups: Sequence[Sequence[Sequence[int]]],
    column_rules: dict | None = None,
    separator: str = " ",
    on_children: str | None = None,
) -> dict:
    """Combine specified fragments using per-column text-joining rules."""
    out = deepcopy_table(table)
    if not isinstance(groups, (list, tuple)) or not groups:
        raise TableTransformError("groups must be a non-empty list")
    if not isinstance(separator, str):
        raise TableTransformError("separator must be a string")
    rules = int_keyed_dict(column_rules, "column_rules") if column_rules else {}
    for col, rule in rules.items():
        _require_choice(rule, ("unique", "concat"), "column_rules")

    body = require_body(out)
    keepers: list[tuple[list[int], dict]] = []
    drop: list[list[int]] = []
    all_paths: list[list[int]] = []
    for g, group in enumerate(groups):
        if not isinstance(group, (list, tuple)) or len(group) < 2:
            raise TableTransformError(f"group {g} must list at least two row paths")
        paths = assert_paths_exist(body, group)
        reject_overlapping_paths(paths, "join_row_fragments")
        all_paths.extend(paths)
        rows = [get_row(body, p) for p in paths]
        width = max(len(row_data_of(r, p)) for r, p in zip(rows, paths))
        merged_data: list[str] = []
        for col in range(width):
            values = []
            for r, p in zip(rows, paths):
                data = row_data_of(r, p)
                val = data[col] if col < len(data) else ""
                values.append(val)
            rule = rules.get(col, "unique")
            nonempty = [v for v in values if v != ""]
            if rule == "concat":
                merged_data.append(separator.join(nonempty))
            else:
                distinct = list(dict.fromkeys(nonempty))
                if len(distinct) > 1:
                    raise TableTransformError(
                        f"group {g} column {col} has conflicting values {distinct!r}; "
                        "use column_rules concat or pass a single value per column"
                    )
                merged_data.append(distinct[0] if distinct else "")

        child_lists = []
        for r in rows:
            kids = r.get("children") or []
            if not isinstance(kids, list):
                raise TableTransformError("children must be a list")
            child_lists.append(kids)
        nonempty_kids = [c for c in child_lists if c]
        if len(nonempty_kids) > 1:
            handling = _require_choice(
                on_children, ("first", "last", "concat"), "on_children"
            ) if on_children is not None else None
            if handling is None:
                raise TableTransformError(
                    f"group {g} has children on more than one fragment; "
                    "pass on_children='first', 'last', or 'concat'"
                )
            if handling == "first":
                children = copy.deepcopy(nonempty_kids[0])
            elif handling == "last":
                children = copy.deepcopy(nonempty_kids[-1])
            else:
                children = []
                for kids in child_lists:
                    children.extend(copy.deepcopy(kids))
        elif len(nonempty_kids) == 1:
            children = copy.deepcopy(nonempty_kids[0])
        else:
            children = []

        keeper = dict(rows[0])
        keeper["row_data"] = merged_data
        keeper["children"] = children
        keepers.append((paths[0], keeper))
        drop.extend(paths[1:])

    reject_overlapping_paths(all_paths, "join_row_fragments")
    for path, keeper in keepers:
        original = get_row(body, path)
        original.clear()
        original.update(keeper)
    if drop:
        out["body"] = _delete_body_paths(require_body(out), drop)
    return out


def expand_merged_cells(table: dict, ranges: Sequence[dict]) -> dict:
    """Repeat a value across explicitly supplied merged-cell ranges only."""
    out = deepcopy_table(table)
    if not isinstance(ranges, (list, tuple)) or not ranges:
        raise TableTransformError("ranges must be a non-empty list")
    for i, spec in enumerate(ranges):
        if not isinstance(spec, dict):
            raise TableTransformError(f"range {i} must be an object")
        region = _require_choice(spec.get("region"), ("header", "body"), "region")
        start = as_index(spec.get("start"), "start")
        end = as_index(spec.get("end"), "end")
        if start < 0 or end < start:
            raise TableTransformError(f"range {i} has invalid start/end")
        value = spec.get("value", _MISSING)
        if value is not _MISSING:
            require_string_cell(value, f"range {i} value")

        targets: list[list[Any]]
        if "paths" in spec:
            if region != "body":
                raise TableTransformError(f"range {i} paths are only valid for body rows")
            targets = [_region_cells(out, "body", as_path(p, "path")) for p in spec["paths"]]
        elif region == "header":
            if "path" in spec:
                targets = [_region_cells(out, "header", as_path(spec["path"], "path"))]
            elif "row" in spec:
                targets = [_region_cells(out, "header", [as_index(spec["row"], "row")])]
            else:
                raise TableTransformError(f"range {i} needs path or row")
        else:
            targets = [_region_cells(out, "body", as_path(spec.get("path"), "path"))]

        for cells in targets:
            if end >= len(cells):
                raise TableTransformError(
                    f"range {i} end {end} is out of range for a row of length {len(cells)}"
                )
            if value is _MISSING:
                found = next((c for c in cells[start : end + 1] if c != ""), None)
                if found is None or not isinstance(found, str):
                    raise TableTransformError(
                        f"range {i} has no value to expand; pass value=... explicitly"
                    )
                fill = found
            else:
                fill = value
            for col in range(start, end + 1):
                cells[col] = fill
    return out


def normalize_width(table: dict, width: int) -> dict:
    """Pad short rows with empty strings to ``width``. Never truncates."""
    out = deepcopy_table(table)
    width = as_index(width, "width")
    if width < 0:
        raise TableTransformError("width must be >= 0")

    def pad_or_reject(cells: list[Any], loc: str) -> list[Any]:
        if len(cells) > width:
            raise TableTransformError(
                f"{loc} has {len(cells)} cells; normalize_width will not truncate to {width}"
            )
        return list(cells) + [""] * (width - len(cells))

    headers = require_headers(out)
    for i, row in enumerate(headers):
        if not isinstance(row, list):
            raise TableTransformError(f"header row {i} is not a list")
        headers[i] = pad_or_reject(row, f"header row {i}")

    def rec(rows: list[Any], prefix: tuple[int, ...]) -> None:
        for i, row in enumerate(rows):
            path = prefix + (i,)
            if not isinstance(row, dict):
                raise TableTransformError(f"body row at {format_path(path)} is not an object")
            data = row.get("row_data")
            if not isinstance(data, list):
                raise TableTransformError(f"row {format_path(path)} is missing a row_data list")
            row["row_data"] = pad_or_reject(data, f"row {format_path(path)}")
            kids = row.get("children")
            if isinstance(kids, list):
                rec(kids, path)

    rec(require_body(out), ())
    return out


def validate_table(table: dict) -> list[dict]:
    """Return structural issues with locations: malformed rows, widths, and value types."""
    issues: list[dict] = []
    if not isinstance(table, dict):
        return [{"code": "malformed_table", "message": "table must be a JSON object"}]

    headers = table.get("headers")
    body = table.get("body")
    if headers is None:
        issues.append({"code": "malformed_table", "message": "missing headers", "region": "header"})
        headers = []
    elif not isinstance(headers, list):
        issues.append({"code": "malformed_table", "message": "headers is not a list", "region": "header"})
        headers = []
    if body is None:
        issues.append({"code": "malformed_table", "message": "missing body", "region": "body"})
        body = []
    elif not isinstance(body, list):
        issues.append({"code": "malformed_table", "message": "body is not a list", "region": "body"})
        body = []

    header_widths: list[int] = []
    for i, row in enumerate(headers):
        loc = {"region": "header", "path": [i]}
        if not isinstance(row, list):
            issues.append({**loc, "code": "malformed_row", "message": f"header row {i} is not a list"})
            continue
        header_widths.append(len(row))
        for j, cell in enumerate(row):
            if not isinstance(cell, str):
                issues.append(
                    {
                        **loc,
                        "column": j,
                        "code": "invalid_type",
                        "message": f"header [{i}, {j}] has type {type(cell).__name__}, expected string",
                    }
                )
    body_widths: list[tuple[list[int], int]] = []

    def walk(rows: Any, prefix: list[int]) -> None:
        if not isinstance(rows, list):
            issues.append(
                {
                    "region": "body",
                    "path": prefix,
                    "code": "malformed_row",
                    "message": f"children at {format_path(prefix) if prefix else 'root'} is not a list",
                }
            )
            return
        for i, row in enumerate(rows):
            path = prefix + [i]
            loc = {"region": "body", "path": path}
            if not isinstance(row, dict):
                issues.append({**loc, "code": "malformed_row", "message": f"row {format_path(path)} is not an object"})
                continue
            data = row.get("row_data")
            if not isinstance(data, list):
                issues.append({**loc, "code": "malformed_row", "message": f"row {format_path(path)} is missing row_data"})
            else:
                body_widths.append((path, len(data)))
                for j, cell in enumerate(data):
                    if not isinstance(cell, str):
                        issues.append(
                            {
                                **loc,
                                "column": j,
                                "code": "invalid_type",
                                "message": f"row {format_path(path)} column {j} has type {type(cell).__name__}, expected string",
                            }
                        )
            kids = row.get("children")
            if "children" in row and kids is not None and not isinstance(kids, list):
                issues.append({**loc, "code": "malformed_row", "message": f"row {format_path(path)} children is not a list"})
            elif isinstance(kids, list):
                walk(kids, path)

    walk(body, [])

    widths = header_widths + [w for _p, w in body_widths]
    if widths:
        expected = max(widths)
        for i, w in enumerate(header_widths):
            if w != expected:
                issues.append(
                    {
                        "region": "header",
                        "path": [i],
                        "code": "inconsistent_width",
                        "width": w,
                        "expected": expected,
                        "message": f"header row {i} has {w} cells (expected {expected})",
                    }
                )
        for path, w in body_widths:
            if w != expected:
                issues.append(
                    {
                        "region": "body",
                        "path": path,
                        "code": "inconsistent_width",
                        "width": w,
                        "expected": expected,
                        "message": f"row {format_path(path)} has {w} cells (expected {expected})",
                    }
                )
    return issues


_RECIPE_OPS = None


def _recipe_merge(table: dict, tables: Sequence[dict] | None = None, **kwargs: Any) -> dict:
    extra = list(tables or [])
    return merge_tables([table, *extra], **kwargs)


def apply_recipe(table: dict, recipe: Sequence[dict]) -> dict:
    """Execute an ordered JSON list of named operations. Returns only if every step succeeds."""
    global _RECIPE_OPS
    if _RECIPE_OPS is None:
        _RECIPE_OPS = {
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
            "merge_tables": _recipe_merge,
            "remove_repeated_headers": remove_repeated_headers,
            "join_row_fragments": join_row_fragments,
            "expand_merged_cells": expand_merged_cells,
            "normalize_width": normalize_width,
        }
    if not isinstance(recipe, (list, tuple)) or not recipe:
        raise TableTransformError("recipe must be a non-empty list of operations")
    result = deepcopy_table(table)
    for i, step in enumerate(recipe):
        if not isinstance(step, dict) or "op" not in step:
            raise TableTransformError(f"recipe step {i} must be an object with 'op'")
        op = step["op"]
        fn = _RECIPE_OPS.get(op)
        if fn is None:
            raise TableTransformError(
                f"recipe step {i}: {op!r} is not a transforming operation "
                "(get_column_paths, validate_table, and split_table cannot be sequenced here)"
            )
        args = {k: copy.deepcopy(v) for k, v in step.items() if k != "op"}
        try:
            result = fn(result, **args)
        except TypeError as e:
            raise TableTransformError(f"recipe step {i} ({op}): {e}") from e
        except TableTransformError as e:
            raise TableTransformError(f"recipe step {i} ({op}): {e}") from e
        if not isinstance(result, dict) or "body" not in result:
            raise TableTransformError(f"recipe step {i} ({op}) did not return a table")
    return result
