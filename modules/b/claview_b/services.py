"""Shared dependencies and input-size guard for A and B runners."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .contracts import Config, Json, StageFailure, Trace
from .metadata import MetadataStore
from .models import SelectionModel
from .search import SearchIndex


@dataclass
class Services:
    store: MetadataStore
    search: SearchIndex
    model: SelectionModel
    config: Config
    trace: Trace


def bounded(context: Json, config: Config):
    if len(json.dumps(context, ensure_ascii=False)) > config.max_context_chars:
        raise StageFailure("context_too_large", "unresolved")
