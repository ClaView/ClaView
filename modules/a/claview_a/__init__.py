"""The new A (plan 37, table-level path): the model reads the raw question and candidate cards and judges at table
level; code checks the judgement. The current-path code it reuses (catalog search, sibling families, basis words,
period helpers, model transport) was extracted into this package (``catalog``, ``search``, ``periods``, ``text``,
``transport``, ``shapes``, ``paths``, ``data``).

Official entry: ``claview_a.run`` (``claview_a.entry.run``); command line: ``python -m claview_a``.
"""


def run(question: str, judge, *, as_of: str, settings=None, layer=None) -> dict:
    """The official entry of the new A (``claview_a.entry.run``), imported on first use so that importing the
    package stays light."""
    from .entry import run as _run

    return _run(question, judge, as_of=as_of, settings=settings, layer=layer)
