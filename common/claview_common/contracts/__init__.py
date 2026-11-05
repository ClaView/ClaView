"""Shared request/response models of the module APIs; see docs/contracts/ for each boundary."""

from .a_http import A_HTTP_VERSION, AHTTPAnswerRequest, AHTTPReply, AHTTPResult, AHTTPSelectRequest
from .b import BRequest, BResult, BTableRef, FetchRequest, FetchResult
from .c import CRequest, ViewModel
from .envelope import Issue, Meta, Outcome
from .period_condition import (
    AppliedPeriodSources,
    BResultData,
    CalculationExtra,
    MonthRange,
    PeriodAnswer,
    PeriodCondition,
    PeriodDifferences,
    PeriodNotice,
    PeriodRange,
    PeriodReference,
    PeriodResolution,
)
from .table import TableRef
from .versions import CONTRACT_VERSION, PERIOD_CONDITION_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "PERIOD_CONDITION_VERSION",
    "A_HTTP_VERSION",
    "AHTTPAnswerRequest",
    "AHTTPReply",
    "AHTTPResult",
    "AHTTPSelectRequest",
    "AppliedPeriodSources",
    "BRequest",
    "BResult",
    "BResultData",
    "BTableRef",
    "CalculationExtra",
    "CRequest",
    "FetchRequest",
    "FetchResult",
    "Issue",
    "Meta",
    "MonthRange",
    "Outcome",
    "PeriodAnswer",
    "PeriodCondition",
    "PeriodDifferences",
    "PeriodNotice",
    "PeriodRange",
    "PeriodReference",
    "PeriodResolution",
    "TableRef",
    "ViewModel",
]
