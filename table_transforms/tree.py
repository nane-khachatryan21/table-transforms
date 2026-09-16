"""Shared helpers for nested table JSON (headers + body rows)."""

from __future__ import annotations

import copy
from typing import Any, Iterable, Sequence

from .errors import TableTransformError

Row = dict[str, Any]
Table = dict[str, Any]


def deepcopy_table(table: Any) -> Table:
    if not isinstance(table, dict):
        raise TableTransformError("table must be a JSON object")
    return copy.deepcopy(table)


def require_headers(table: Table) -> list[Any]:
    headers = table.get("headers")
    if headers is None:
        table["headers"] = []
        return table["headers"]
    if not isinstance(headers, list):
        raise TableTransformError("headers must be a list")
    return headers


def require_body(table: Table) -> list[Any]:
    body = table.get("body")
    if body is None:
        table["body"] = []
        return table["body"]
    if not isinstance(body, list):
        raise TableTransformError("body must be a list")
    return body


def as_index(value: Any, name: str = "index") -> int:
    if isinstance(value, bool):
        raise TableTransformError(f"{name} must be an integer")
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as e:
            raise TableTransformError(f"{name} must be an integer") from e
    if not isinstance(value, int):
        raise TableTransformError(f"{name} must be an integer")
    return value


def as_path(value: Any, name: str = "path") -> list[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise TableTransformError(f"{name} must be a non-empty list of indices, e.g. [2, 1]")
    return [as_index(v, name) for v in value]


def as_optional_path(value: Any, name: str = "parent") -> list[int] | None:
    if value is None or value == [] or value == ():
        return None
    return as_path(value, name)


def format_path(path: Sequence[int]) -> str:
    return "[" + ", ".join(str(i) for i in path) + "]"


def header_width(headers: Sequence[Any]) -> int:
    width = 0
    for row in headers:
        if isinstance(row, list):
            width = max(width, len(row))
    return width


def body_width(rows: Sequence[Any]) -> int:
    width = 0

    def walk(items: Sequence[Any]) -> None:
        nonlocal width
        for row in items:
            if isinstance(row, dict) and isinstance(row.get("row_data"), list):
                width = max(width, len(row["row_data"]))
            if isinstance(row, dict) and isinstance(row.get("children"), list):
                walk(row["children"])

    walk(rows)
    return width


def table_width(table: Table) -> int:
    return max(header_width(table.get("headers") or []), body_width(table.get("body") or []))


def children_of(row: Any) -> list[Any]:
    if not isinstance(row, dict):
        raise TableTransformError("body row must be an object")
    children = row.get("children")
    if children is None:
        row["children"] = []
        return row["children"]
    if not isinstance(children, list):
        raise TableTransformError("children must be a list")
    return children


def get_row(body: list[Any], path: Sequence[int]) -> Row:
    rows = body
    row: Row | None = None
    for i, idx in enumerate(path):
        idx = as_index(idx, "path")
        if idx < 0 or not isinstance(rows, list) or idx >= len(rows):
            raise TableTransformError(f"row path {format_path(path)} does not exist")
        row = rows[idx]
        if not isinstance(row, dict):
            raise TableTransformError(f"body row at {format_path(path[: i + 1])} is not an object")
        if i < len(path) - 1:
            rows = row.get("children")
            if rows is None:
                rows = []
            elif not isinstance(rows, list):
                raise TableTransformError(f"row {format_path(path[: i + 1])} has no children list")
    assert row is not None
    return row


def parent_children(table: Table, parent: Sequence[int] | None) -> list[Any]:
    if parent is None:
        return require_body(table)
    parent_row = get_row(require_body(table), parent)
    return children_of(parent_row)


def row_data_of(row: Row, path: Sequence[int] | None = None) -> list[Any]:
    data = row.get("row_data")
    if not isinstance(data, list):
        loc = format_path(path) if path is not None else "row"
        raise TableTransformError(f"{loc} is missing a row_data list")
    return data


def require_string_cell(value: Any, loc: str) -> str:
    if not isinstance(value, str):
        raise TableTransformError(f"{loc} value must be a string")
    return value


def clone_row(row: Any) -> Row:
    if not isinstance(row, dict):
        raise TableTransformError("row must be an object")
    return copy.deepcopy(row)


def coerce_inserted_row(row: Any) -> Row:
    """Validate a caller-supplied row and return an independent copy."""
    if not isinstance(row, dict):
        raise TableTransformError("inserted row must be an object with row_data")
    data = row.get("row_data")
    if not isinstance(data, list):
        raise TableTransformError("inserted row must contain a row_data list")
    for i, cell in enumerate(data):
        if not isinstance(cell, str):
            raise TableTransformError(f"inserted row cell {i} must be a string")
    children = row.get("children", [])
    if children is None:
        children = []
    if not isinstance(children, list):
        raise TableTransformError("inserted row children must be a list")
    out = copy.deepcopy(row)
    out["row_data"] = list(data)
    out["children"] = [coerce_inserted_row(child) for child in children]
    return out


def path_is_prefix(prefix: Sequence[int], path: Sequence[int]) -> bool:
    return len(path) >= len(prefix) and list(path[: len(prefix)]) == list(prefix)


def assert_paths_exist(body: list[Any], paths: Iterable[Sequence[int]]) -> list[list[int]]:
    if not isinstance(paths, (list, tuple)):
        raise TableTransformError("paths must be a list of row paths")
    resolved = [as_path(p) for p in paths]
    if not resolved:
        raise TableTransformError("paths must be a non-empty list")
    for path in resolved:
        get_row(body, path)
    return resolved


def reject_overlapping_paths(paths: Sequence[Sequence[int]], action: str) -> None:
    tuples = [tuple(as_path(p)) for p in paths]
    for i, a in enumerate(tuples):
        for b in tuples[i + 1 :]:
            if path_is_prefix(a, b) or path_is_prefix(b, a):
                raise TableTransformError(
                    f"{action} is ambiguous because {format_path(a)} overlaps {format_path(b)}"
                )


def preorder(rows: Sequence[Any]) -> list[tuple[list[int], int, Row]]:
    out: list[tuple[list[int], int, Row]] = []

    def walk(items: Sequence[Any], depth: int, prefix: Sequence[int]) -> None:
        for i, row in enumerate(items):
            path = list(prefix) + [i]
            if not isinstance(row, dict):
                raise TableTransformError(f"body row at {format_path(path)} is not an object")
            out.append((path, depth, row))
            kids = row.get("children")
            if isinstance(kids, list):
                walk(kids, depth + 1, path)

    walk(rows, 0, ())
    return out


def reconstruct_body(flat: Sequence[tuple[int, Row]]) -> list[Row]:
    """Rebuild a forest from (depth, row) pairs. Children on input rows are ignored."""
    if not flat:
        return []
    for i in range(1, len(flat)):
        if flat[i][0] > flat[i - 1][0] + 1:
            raise TableTransformError(
                f"invalid hierarchy: depth jumps from {flat[i - 1][0]} to {flat[i][0]} at row {i}"
            )
        if flat[i][0] < 0 or flat[i - 1][0] < 0:
            raise TableTransformError("row depth must be >= 0")
    if flat[0][0] < 0:
        raise TableTransformError("row depth must be >= 0")

    root: list[Row] = []
    stack: list[tuple[int, list[Row]]] = [(-1, root)]
    for depth, src in flat:
        if not isinstance(src, dict):
            raise TableTransformError("body row must be an object")
        while stack[-1][0] >= depth:
            stack.pop()
        node = copy.deepcopy(src)
        node["row_data"] = list(row_data_of(src))
        node["children"] = []
        stack[-1][1].append(node)
        stack.append((depth, node["children"]))
    return root


def remap_path_after_deletes(path: Sequence[int], deleted: Sequence[Sequence[int]]) -> list[int]:
    deleted_t = [tuple(p) for p in deleted]
    for d in deleted_t:
        if path_is_prefix(d, path) and tuple(d) != tuple(path):
            raise TableTransformError(
                f"destination {format_path(path)} lies inside deleted subtree {format_path(d)}"
            )
    new: list[int] = []
    for depth, idx in enumerate(path):
        orig_prefix = tuple(path[:depth])
        shift = sum(
            1
            for dp in deleted_t
            if len(dp) == depth + 1 and dp[:-1] == orig_prefix and dp[-1] < idx
        )
        new.append(idx - shift)
    return new


def apply_column_plan(cells: Sequence[Any], sources: Sequence[int | None], fill: str = "") -> list[Any]:
    out: list[Any] = []
    for src in sources:
        if src is None:
            out.append(fill)
        elif isinstance(cells, list) and 0 <= src < len(cells):
            out.append(cells[src])
        else:
            out.append(fill)
    return out


def map_headers(headers: Sequence[Any], sources: Sequence[int | None], fill: str = "") -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in headers:
        if not isinstance(row, list):
            raise TableTransformError("header row must be a list")
        out.append(apply_column_plan(row, sources, fill=fill))
    return out


def map_body_columns(rows: Sequence[Any], sources: Sequence[int | None], fill: str = "") -> list[Row]:
    out: list[Row] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TableTransformError("body row must be an object")
        node = copy.deepcopy(row)
        data = row.get("row_data")
        if not isinstance(data, list):
            raise TableTransformError("body row is missing a row_data list")
        node["row_data"] = apply_column_plan(data, sources, fill=fill)
        kids = row.get("children") or []
        if not isinstance(kids, list):
            raise TableTransformError("children must be a list")
        node["children"] = map_body_columns(kids, sources, fill=fill)
        out.append(node)
    return out


def resolve_column_mapping(
    n_cols: int,
    mapping: Any,
    exclude: Any = None,
) -> list[int | None]:
    """Return output-column source indices (None = empty column)."""
    if n_cols < 0:
        raise TableTransformError("column count must be >= 0")
    excluded: set[int] = set()
    if exclude is not None:
        if not isinstance(exclude, (list, tuple, set)):
            raise TableTransformError("exclude must be a list of source column indices")
        for item in exclude:
            idx = as_index(item, "exclude")
            if idx < 0 or idx >= n_cols:
                raise TableTransformError(f"excluded column {idx} is out of range")
            if idx in excluded:
                raise TableTransformError(f"column {idx} is listed twice in exclude")
            excluded.add(idx)

    if mapping is None:
        mapping = list(range(n_cols))

    if isinstance(mapping, dict):
        occupied: dict[int, int] = {}
        mapped_sources: set[int] = set()
        for raw_src, raw_tgt in mapping.items():
            src = as_index(raw_src, "mapping source")
            tgt = as_index(raw_tgt, "mapping target")
            if src < 0 or src >= n_cols:
                raise TableTransformError(f"mapped source column {src} is out of range")
            if tgt < 0:
                raise TableTransformError(f"mapped target column {tgt} must be >= 0")
            if src in excluded:
                raise TableTransformError(f"column {src} is both mapped and excluded")
            if src in mapped_sources:
                raise TableTransformError(f"source column {src} is mapped more than once")
            if tgt in occupied:
                raise TableTransformError(
                    f"target column {tgt} is mapped from both {occupied[tgt]} and {src}"
                )
            occupied[tgt] = src
            mapped_sources.add(src)
        preserved = [i for i in range(n_cols) if i not in mapped_sources and i not in excluded]
        max_tgt = max(occupied) if occupied else -1
        out: list[int | None] = [None] * (max_tgt + 1)
        for tgt, src in occupied.items():
            out[tgt] = src
        for i, slot in enumerate(out):
            if slot is None:
                if preserved:
                    out[i] = preserved.pop(0)
        out.extend(preserved)
        return out

    if not isinstance(mapping, (list, tuple)):
        raise TableTransformError("mapping must be a list of source indices or a source-to-target dict")

    order: list[int | None] = []
    seen: set[int] = set()
    for item in mapping:
        src = as_index(item, "mapping")
        if src < 0 or src >= n_cols:
            raise TableTransformError(f"mapped source column {src} is out of range")
        if src in excluded:
            raise TableTransformError(f"column {src} is both mapped and excluded")
        if src in seen:
            raise TableTransformError(f"source column {src} appears twice in mapping")
        seen.add(src)
        order.append(src)
    for i in range(n_cols):
        if i not in seen and i not in excluded:
            order.append(i)
    return order


def insert_index(n: int, index: Any, name: str = "index") -> int:
    if index is None:
        return n
    idx = as_index(index, name)
    if idx < 0 or idx > n:
        raise TableTransformError(f"{name} {idx} is out of range for length {n}")
    return idx


def int_keyed_dict(value: Any, name: str) -> dict[int, Any]:
    if not isinstance(value, dict):
        raise TableTransformError(f"{name} must be an object")
    out: dict[int, Any] = {}
    for k, v in value.items():
        out[as_index(k, name)] = v
    return out
