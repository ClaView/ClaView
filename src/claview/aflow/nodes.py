"""A v1 node assembly: the stage modules (q_nodes, s_nodes, j_nodes, t_nodes; shared helpers in
node_common) provide the 24 nodes; this module builds NODES and applies the requirement-local wrappers.

Names used by scoped_nodes, graph and the tests stay importable from here (claview.aflow.nodes.X).
"""

from __future__ import annotations

from .contract import CANDIDATE_CARDS  # noqa: F401  (re-exported for scoped_nodes)
from .node_common import (  # noqa: F401  (re-exported)
    _channel,
)
from .q_nodes import (  # noqa: F401  (re-exported)
    _CatalogNames,
    _catalog_names,
    ambiguous_conditions,
    q1,
    q2,
    q3,
    q4,
    q5,
    q6,
    q7,
)
from .s_nodes import (  # noqa: F401  (re-exported)
    CHANNELS,
    POOL_CAP,
    route_after_s7,
    s1,
    s2,
    s3,
    s5,
    s6,
    s7,
)
from .j_nodes import (  # noqa: F401  (re-exported)
    TITLE_BONUS,
    _byeol_linked,
    _envelope,
    _fit_basis,
    _judge,
    _match_method,
    _title_condition_forms,
    _title_stem,
    card_rows,
    cross_axis_support,
    j1,
    j2,
    j3,
    j4,
    j5,
    j7,
)
from .t_nodes import (  # noqa: F401  (re-exported)
    _alternatives,
    _cross_axis_judge,
    _cross_hold,
    _whole_question_pick,
    t1,
    t2,
    t3,
    t4,
    t5,
)


NODES = {
    "Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "Q5": q5, "Q6": q6, "Q7": q7,
    "S1": s1, "S2": s2, "S3": s3, "S5": s5, "S6": s6, "S7": s7,
    "J1": j1, "J2": j2, "J3": j3, "J4": j4, "J5": j5, "J7": j7,
    "T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5,
}

# Keep the original pure node functions as the rule-path implementation. The
# adapters add requirement-local work only after successful Q6g interpretation.
from .scoped_nodes import adapt as _adapt_scoped_nodes
NODES = _adapt_scoped_nodes(NODES)
for _name, _node in NODES.items():
    globals()[_name.lower()] = _node
