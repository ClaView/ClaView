"""Public entry point of the catalog layer: load everything in one call.

    layer = load_layer()                      # real catalog, draft overrides on, external synonyms when present
    layer.index.find_spans("...")             # name-preserving spans
    layer.roles.value(table_id, obj_id, item_id).type
    layer.name_spans().find("...")            # name spans that respect word boundaries (plan 04, 2-5)
    layer.list_names()                        # catalog name lookups for the list rules of find_conditions

Switches: ``overrides`` (draft axis-role overrides), ``use_draft_csv`` (user's per-axis CSVs),
``include_external_synonyms`` (external synonym draft, skipped when the file is absent). All paths default to
``claview_a.paths`` and can be replaced (tests use synthetic files). ``name_spans`` and ``list_names`` are built on
first use and kept on the layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .index import TermIndex, build_index
from .loader import Catalog, load_catalog
from .names import CatalogNames
from .out_of_scope import OutOfScopeList, load_out_of_scope
from .roles import AxisRoleTable, build_axis_roles
from .spans import NameSpanIndex, build_name_span_index
from .synonyms import SynonymDict, load_synonyms


@dataclass
class CatalogLayer:
    catalog: Catalog
    roles: AxisRoleTable
    synonyms: SynonymDict
    out_of_scope: OutOfScopeList
    index: TermIndex
    _derived: dict = field(default_factory=dict, repr=False, compare=False)

    def name_spans(self) -> NameSpanIndex:
        """Word-boundary name spans over the term index plus year-range-free names (built once)."""
        if "name_spans" not in self._derived:
            self._derived["name_spans"] = build_name_span_index(self.index)
        return self._derived["name_spans"]

    def list_names(self) -> CatalogNames:
        """Catalog value and table-name lookups used by the list and exclude rules (built once)."""
        if "list_names" not in self._derived:
            self._derived["list_names"] = CatalogNames(self.catalog)
        return self._derived["list_names"]


def load_layer(*, search_catalog: Path | None = None, meta_raw: Path | None = None, overrides: bool = True,
               use_draft_csv: bool = True, include_external_synonyms: bool = True,
               layer1_path: Path | None = None, generic_path: Path | None = None, collapsed_path: Path | None = None,
               overrides_path: Path | None = None, basis_words_path: Path | None = None,
               synonyms_approved_path: Path | None = None, external_synonyms_path: Path | None = None,
               out_of_scope_path: Path | None = None) -> CatalogLayer:
    catalog = load_catalog(search_catalog, meta_raw)
    roles = build_axis_roles(catalog, use_draft_csv=use_draft_csv, overrides=overrides, layer1_path=layer1_path,
                             generic_path=generic_path, collapsed_path=collapsed_path, overrides_path=overrides_path,
                             basis_words_path=basis_words_path)
    synonyms = load_synonyms(synonyms_approved_path, external_synonyms_path, include_external_synonyms)
    oos = load_out_of_scope(out_of_scope_path)
    index = build_index(catalog, roles, synonyms, oos)
    return CatalogLayer(catalog, roles, synonyms, oos, index)
