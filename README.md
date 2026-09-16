# table-transforms

Python library for **deterministic** edits on hierarchical table JSON, plus a local **Table Studio** UI.

Every function takes a table (or tables), returns a new table, and never mutates its input. If a request is incomplete, overlapping, or ambiguous, the call raises `TableTransformError` instead of guessing.

```python
from table_transforms import edit_cells, TableTransformError

table = {
    "headers": [["Year", "Revenue"]],
    "body": [
        {"row_data": ["2024", "10"], "children": []},
        {"row_data": ["2025", "12"], "children": []},
    ],
}

updated = edit_cells(table, [
    {"region": "body", "path": [1], "column": 1, "value": "15"},
])
```

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Table JSON](#table-json)
- [Library usage](#library-usage)
- [Table Studio UI](#table-studio-ui)
- [Design rules](#design-rules)
- [Function reference](#function-reference)
- [Recipes](#recipes)
- [Errors](#errors)

## Requirements

- Python 3.9+
- No third-party packages for the library or the UI server (the UI loads React, AG Grid, and Tailwind from a CDN in the browser)

## Install

```bash
git clone https://github.com/nane-khachatryan21/table-transforms.git
cd table-transforms
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

After that, `import table_transforms` works from any working directory. You can also skip the install and run from the repo root: Python will find the `table_transforms/` package because the current directory is on `sys.path`.

## Table JSON

A table is a JSON object with a header matrix and a nested body:

```json
{
  "headers": [
    ["Region", "Region", "2024"],
    ["Country", "City", "Revenue"]
  ],
  "body": [
    {
      "row_data": ["EMEA", "", ""],
      "children": [
        {"row_data": ["France", "Paris", "10"], "children": []},
        {"row_data": ["UK", "London", "8"], "children": []}
      ]
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `headers` | List of header **rows**. Each row is a list of strings. Multiple rows are stacked header levels, not a single flattened path. |
| `body` | List of row objects. |
| `row_data` | Cells for that row, as strings. |
| `children` | Nested rows under this row (use `[]` when there are none). |

**Row paths** address nested body rows. `[0]` is the first top-level row. `[0, 1]` is the second child of that row. Header rows use a single index such as `[1]` (the second header row).

Column indices are 0-based, left to right.

Cells are strings. Transforms will not coerce numbers or nulls; `validate_table` reports those as issues.

## Library usage

```python
from table_transforms import (
    TableTransformError,
    apply_recipe,
    delete_rows,
    edit_cells,
    insert_rows,
    move_rows,
    validate_table,
)

table = {
    "headers": [["Item", "Qty"]],
    "body": [
        {"row_data": ["Apples", "2"], "children": []},
        {"row_data": ["Pears", "1"], "children": []},
    ],
}

table = insert_rows(
    table,
    rows=[{"row_data": ["Grapes", "4"], "children": []}],
    index=1,
)
table = edit_cells(
    table,
    changes=[{"region": "body", "path": [0], "column": 1, "value": "3"}],
)
table = move_rows(table, paths=[[1]], parent=None, index=0)
table = delete_rows(table, paths=[[2]])

issues = validate_table(table)
if issues:
    print(issues)

# Same work as a replayable recipe:
table = apply_recipe(table, [
    {"op": "normalize_width", "width": 2},
    {"op": "edit_cells", "changes": [
        {"region": "header", "path": [0], "column": 1, "value": "Quantity"},
    ]},
])
```

Invalid or underspecified calls raise `TableTransformError` (a `ValueError`). Catch it at the boundary; do not retry with inferred arguments.

## Table Studio UI

Table Studio is a local browser app over the same functions. The grid never edits JSON in JavaScript: every change is `POST /api/preview` or `POST /api/apply` on the Python server.

### Start the server

From the repo root, after `pip install -e .`:

```bash
python -m table_transforms
```

or:

```bash
python -m table_transforms.ui
```

or:

```bash
table-studio
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/). The process prints the URL and tries to open a browser unless you disable that.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `TABLE_STUDIO_HOST` | `127.0.0.1` | Bind address |
| `TABLE_STUDIO_PORT` | `8765` | Port |
| `TABLE_STUDIO_NO_BROWSER` | unset | Set to `1` to skip opening a browser |

```bash
TABLE_STUDIO_PORT=9000 TABLE_STUDIO_NO_BROWSER=1 python -m table_transforms
```

### Load tables

Upload a **JSONL** file. Each line is one table record. Either of these shapes works:

```json
{"image": "images/page-003.png", "target": {"headers": [["A", "B"]], "body": [{"row_data": ["1", "2"], "children": []}]}}
```

```json
{"image": "images/page-003.png", "headers": [["A", "B"]], "body": [{"row_data": ["1", "2"], "children": []}]}
```

`image` (or `image_path` / `path`) is used as the table name and to match screenshot files if you also upload an images folder. Extra fields on the record are kept and written back on export.

### How to work in the UI

1. Select one or more tables in the left sidebar.
2. Edit cells in the grid, or pick a transform in the right panel.
3. Most bulk operations **preview first**. Confirm to apply, or press Esc to discard.
4. Undo / redo are session-wide (all tables).
5. Export writes a corrected JSONL of the current tables.

The divider between the image and the grid is draggable (double-click to reset). Sidebars collapse; Focus mode hides them.

### Keyboard

| Shortcut | Action |
| --- | --- |
| Cmd+Z / Ctrl+Z | Undo |
| Shift+Cmd+Z / Ctrl+Shift+Z / Ctrl+Y | Redo |
| Cmd+S / Ctrl+S | Export JSONL |
| Cmd+B / Ctrl+B | Toggle tables sidebar |
| Cmd+\\ / Ctrl+\\ | Toggle transforms panel |
| Shift+Cmd+F / Ctrl+Shift+F | Focus mode |
| Cmd+Backspace / Ctrl+Backspace | Delete selected rows |
| Arrow keys | Move between cells |
| Tab / Enter | Next column / next row |
| Alt+Left | Outdent |
| Alt+Right | Indent |
| Alt+Up | Move row up among siblings |
| Alt+Down | Move row down among siblings |
| Esc | Cancel preview |
| ? | Shortcut list |

### HTTP API

The UI is the supported front end. The server also exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/state?id=` | Session: table list, active table, undo flags |
| `POST` | `/api/load` | `{ "records": [...], "doc_name": "labels.jsonl" }` or `{ "jsonl": "..." }` |
| `POST` | `/api/preview` | `{ "op": "...", "table_ids": [...], "args": { ... } }` (no commit) |
| `POST` | `/api/apply` | Same body as preview; commit |
| `POST` | `/api/undo` | Restore previous snapshot |
| `POST` | `/api/redo` | Re-apply undone snapshot |
| `POST` | `/api/recipes` | `{ "name": "...", "steps": [ ... ] }` |
| `GET` | `/api/export.jsonl` | Download current tables as JSONL |

`op` is a public function name from this library (`edit_cells`, `merge_tables`, and so on). Arguments match that function, minus `table` / `tables`: the server supplies those from `table_ids`.

## Design rules

These constraints are intentional:

- **No inference.** Column alignment, header detection, merge parents, and split cuts must be specified.
- **Inputs are copied.** Callers can keep the original table.
- **Strings only** in cells.
- **Nested body, stacked headers.** Header *paths* such as `"Region / Country"` are a display concern (`get_column_paths`); they are not the stored shape.
- **Ambiguity is an error.** Example: joining fragments that disagree in a column without `column_rules`, or moving a parent into its own child.
- **`split_table` returns a list** of tables, so it cannot appear inside `apply_recipe`.

## Function reference

All transforming functions return a new `dict` unless noted. Inspection helpers return lists.

### `edit_cells(table, changes)`

Write explicit cell values in the header or body.

Each item in `changes`:

| Key | Type | Notes |
| --- | --- | --- |
| `region` | `"header"` or `"body"` | Required |
| `path` | list of int | Body: row path. Header: `[row_index]`. |
| `row` | int | Header only, instead of `path` |
| `column` | int | 0-based. May append one cell if `column == len(row)`. |
| `value` | str | Required |

```python
edit_cells(table, [
    {"region": "header", "row": 0, "column": 0, "value": "Year"},
    {"region": "body", "path": [0, 1], "column": 2, "value": "n/a"},
])
```

---

### `insert_rows(table, rows, parent=None, index=None)`

Insert supplied row objects (they must already have `row_data` and may have `children`).

| Arg | Default | Notes |
| --- | --- | --- |
| `rows` | required | Non-empty list of `{ "row_data": [...], "children": [...] }` |
| `parent` | `None` | Path of the parent row; `None` = top-level body |
| `index` | `None` | Insert before this sibling index; `None` = append |

---

### `delete_rows(table, paths)`

Remove the listed body rows **and their subtrees**.

---

### `duplicate_rows(table, paths)`

Clone each listed row (including children) and insert the copy immediately after the original.

---

### `move_rows(table, paths, parent=None, index=None)`

Move rows with their subtrees (reorder, indent, outdent).

- `parent=None` sends them to the top-level body.
- Cannot move a row into its own descendant.
- Paths must not overlap (you cannot move both a parent and a child in one call).

---

### `insert_columns(table, index, count=1, fill="")`

Insert `count` columns at `index` in every header row and every nested body row. New cells are `fill` (a string). `index` may equal the current width (append) but must not skip past it.

---

### `delete_columns(table, columns)`

Drop the listed column indices everywhere. Duplicates and out-of-range indices are errors.

---

### `map_columns(table, mapping, exclude=None)`

Reorder / realign columns with an explicit mapping. Unmapped columns stay in their original order unless listed in `exclude`.

`mapping` is either:

- a **list** of source indices: those columns come first, in that order; remaining columns follow
- a **dict** of `{source: target}`: sources are placed at the given target indices; gaps fill from unmapped columns

```python
map_columns(table, mapping=[2, 0, 1])          # permutation prefix
map_columns(table, mapping={0: 2}, exclude=[1])  # column 0 -> 2; drop 1
```

---

### `set_headers(table, headers=None, edits=None)`

Replace the header matrix and/or patch individual header cells. At least one of `headers` or `edits` is required.

```python
set_headers(table, headers=[["A", "B"], ["a1", "b1"]])
set_headers(table, edits=[{"row": 0, "column": 1, "value": "B"}])
```

---

### `get_column_paths(table, levels=None, separator=" / ")`

**Inspection** (does not modify the table). Joins selected header rows into one label per column, skipping blanks and consecutive duplicates. Duplicate labels get a `[column]` suffix.

```python
get_column_paths(table)
# ["Region / Country", "Region / City", "2024 / Revenue"]

get_column_paths(table, levels=[1], separator=" | ")
```

`levels` is a list of header-row indices. Default: every header row.

---

### `transfer_header_rows(table, direction, mode, rows, parent=None, index=None, at=None, on_children=None)`

Copy or move rows between the header matrix and the body.

| Arg | Values |
| --- | --- |
| `direction` | `"to_body"` or `"to_headers"` |
| `mode` | `"copy"` or `"move"` |
| `rows` | `to_body`: header row indices. `to_headers`: body paths. |
| `parent`, `index` | Where to insert in the body (`to_body`) |
| `at` | Header insert index (`to_headers`) |
| `on_children` | See below |

When sending a **body row that has children** to headers:

- `copy` copies only that row unless `on_children="flatten"` (also copy descendants as extra header rows).
- `move` **requires** `on_children`:
  - `"flatten"`: the row and descendants become header rows; they leave the body
  - `"promote"`: only the row becomes a header; children remain in the body, promoted one level

---

### `rebuild_hierarchy(table, start, end=None, depths=None, parents=None)`

Rebuild nesting for a contiguous preorder range of body rows. Cell text and row order are kept; only parent/child links change.

Provide **exactly one** of:

- `depths`: absolute depth per row in the range (`0` = top-level)
- `parents`: for each row, `None` (range-local root) or the **index within the range** of its parent

`start` is a body path. If `end` is omitted, the range is `start` plus its current descendants.

```python
rebuild_hierarchy(
    table,
    start=[2],
    end=[5],
    depths=[0, 1, 1, 0],
)
```

---

### `merge_tables(tables, column_maps=None, headers="first", attachments=None)`

Concatenate two or more tables. This is the one public function whose first argument is a **list of tables**, not a single table.

| Arg | Default | Notes |
| --- | --- | --- |
| `tables` | required | At least two table dicts, in order |
| `column_maps` | `None` | One mapping per table, same shapes as `map_columns` (`None` = identity) |
| `headers` | `"first"` | `"first"` keep table 0 headers; `"concat"` stack all header blocks; `"none"` empty headers; or an explicit matrix |
| `attachments` | `None` | How tables `1..n-1` attach into table 0 instead of appending |

Without `attachments`, each following table's body is appended at the top level.

`attachments` keys are **source table indices** (`1`, `2`, ...). Each value is:

- `{ "parent": [path], "index": 0 }`: insert that table's entire body under a row of the result so far, or
- `{ "row_parents": { 0: [path], 1: null } }`: per incoming **top-level** row, a parent path in the result (`null` / omit = stay in the leftover append list)

JSON object keys are strings; `{"1": {"parent": [0]}}` is accepted.

```python
merge_tables(
    [left, right],
    column_maps=[None, [1, 0, 2]],
    headers="first",
    attachments={1: {"parent": [0], "index": None}},
)
```

In `apply_recipe`, merge is written as extra tables on the step (the recipe's current table is first):

```python
{"op": "merge_tables", "tables": [other], "headers": "first"}
```

---

### `remove_repeated_headers(table, start=None, end=None, paths=None, header_rows=None, on_children=None)`

Delete body rows that reprint the header.

- If `paths` is set, those rows are removed (must lie in `start`/`end` if given).
- Otherwise, body rows whose `row_data` **exactly equals** a header row are removed. `header_rows` limits which header rows to match; default is all of them.
- `start` / `end` limit the preorder range (inclusive).

If a matched row has children, you must pass `on_children`:

- `"promote"`: keep the children, lifted into the parent's place
- `"drop"`: delete the row and its subtree

---

### `split_table(table, boundaries, headers="inherit", subtree="reject")`

Split **before** each listed body path. Returns a **list** of tables, not a single table. Not allowed in `apply_recipe`.

| Arg | Default | Notes |
| --- | --- | --- |
| `boundaries` | required | First row of each *next* piece; must not be `[0]` or empty a piece |
| `headers` | `"inherit"` | `"inherit"` copy headers to every piece; `"first"` only the first piece; `"none"`; or a list of matrices, one per piece |
| `subtree` | `"reject"` | `"reject"` if a cut would orphan children from their parent; `"promote"` to allow that |

```python
first, second = split_table(table, boundaries=[[10]], headers="inherit")
```

---

### `join_row_fragments(table, groups, column_rules=None, separator=" ", on_children=None)`

Merge listed groups of body rows into one row each. The first path in a group is the keeper; the rest are deleted.

| Arg | Default | Notes |
| --- | --- | --- |
| `groups` | required | List of groups; each group is at least two row paths |
| `column_rules` | `None` | `{column: "unique" | "concat"}`. Default per column is `"unique"` |
| `separator` | `" "` | Used when a column is `"concat"` |
| `on_children` | `None` | Required if more than one fragment in a group has children: `"first"`, `"last"`, or `"concat"` |

`"unique"`: all non-empty values in that column must be identical, or the call errors. `"concat"`: join non-empty values with `separator`.

---

### `expand_merged_cells(table, ranges)`

Fill a contiguous column span with one string. Only the ranges you pass are touched; there is no visual merge detection.

Each range:

| Key | Notes |
| --- | --- |
| `region` | `"header"` or `"body"` |
| `start`, `end` | Inclusive column indices |
| `value` | Optional string. If omitted, the first non-empty cell in the span is used |
| `path` / `row` | Which header or body row |
| `paths` | Body only: apply the same span to several rows |

---

### `normalize_width(table, width)`

Pad every header and body row with `""` up to `width`. **Never truncates**: a longer row is an error.

---

### `validate_table(table)`

**Inspection.** Returns a list of issue dicts (empty means structurally fine). Does not raise for ordinary width/type problems.

Typical `code` values: `malformed_table`, `malformed_row`, `invalid_type`, `inconsistent_width`.

Issues include `region`, and `path` / `column` when they apply.

---

### `apply_recipe(table, recipe)`

Run an ordered list of steps. Stops and raises on the first failure. See [Recipes](#recipes).

---

### `TableTransformError`

Raised when arguments are missing, out of range, overlapping, or otherwise ambiguous. Subclass of `ValueError`.

## Recipes

A recipe is a JSON list of objects. Each object has `"op"` plus the keyword arguments of that function (no nested `"args"` wrapper). The table flows through the steps.

**Allowed `op` values:**
`edit_cells`, `insert_rows`, `delete_rows`, `duplicate_rows`, `move_rows`, `insert_columns`, `delete_columns`, `map_columns`, `set_headers`, `transfer_header_rows`, `rebuild_hierarchy`, `merge_tables`, `remove_repeated_headers`, `join_row_fragments`, `expand_merged_cells`, `normalize_width`.

**Not allowed in a recipe:** `get_column_paths`, `validate_table` (they are not transforms), `split_table` (it returns multiple tables).

```python
from table_transforms import apply_recipe

result = apply_recipe(table, [
    {"op": "normalize_width", "width": 4},
    {"op": "remove_repeated_headers", "on_children": "promote"},
    {"op": "edit_cells", "changes": [
        {"region": "header", "path": [0], "column": 0, "value": "Item"},
    ]},
    {"op": "merge_tables", "tables": [continuation], "headers": "first"},
])
```

## Errors

```python
from table_transforms import TableTransformError, move_rows

try:
    move_rows(table, paths=[[0]], parent=[0, 0])
except TableTransformError as e:
    print(e)  # cannot move [0] into its own subtree at [0, 0]
```

Malformed tables (missing `headers` / `body`, non-dict rows) also raise from transforming ops. Use `validate_table` first when the input may be dirty.

## Project layout

```
table_transforms/          # importable package
  __init__.py              # public API
  ops.py                   # transform implementations
  tree.py                  # paths, copying, column maps
  errors.py
  ui/
    server.py              # stdlib HTTP server
    static/index.html      # Table Studio
pyproject.toml
```

Internal helpers in `tree.py` are not part of the supported API.
