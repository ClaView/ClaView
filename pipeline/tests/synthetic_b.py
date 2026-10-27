"""The SYNTHETIC B model and fetch client of the flow tests (wiring checks only).

They live in `claview_pipeline.synthetic`; this module keeps the tests' import name.
"""

from claview_pipeline.synthetic import LABEL, SyntheticFetchClient, SyntheticSelectionModel

__all__ = ["LABEL", "SyntheticFetchClient", "SyntheticSelectionModel"]
