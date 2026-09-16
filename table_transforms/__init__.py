"""Deterministic transformations on hierarchical table JSON.

Operate on a table dict ``{"headers": [[...]], "body": [{"row_data": [...], "children": [...]}]}``.
Each function returns a new dict; inputs are not mutated. Invalid or ambiguous
requests raise ``TableTransformError``.
"""

from .errors import TableTransformError
from .ops import (
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

__all__ = [
    "TableTransformError",
    "edit_cells",
    "insert_rows",
    "delete_rows",
    "duplicate_rows",
    "move_rows",
    "insert_columns",
    "delete_columns",
    "map_columns",
    "set_headers",
    "get_column_paths",
    "transfer_header_rows",
    "rebuild_hierarchy",
    "merge_tables",
    "remove_repeated_headers",
    "split_table",
    "join_row_fragments",
    "expand_merged_cells",
    "normalize_width",
    "apply_recipe",
    "validate_table",
]
