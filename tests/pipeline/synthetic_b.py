"""The SYNTHETIC B model and fetch client of the flow tests (wiring checks only).

They live in `claview.pipeline.synthetic`; this module keeps the tests' import name.
"""

from claview.pipeline.synthetic import LABEL, SyntheticFetchClient, SyntheticSelectionModel

__all__ = ["LABEL", "SyntheticFetchClient", "SyntheticSelectionModel"]
