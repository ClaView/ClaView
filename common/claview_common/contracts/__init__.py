"""Request/response models of the module APIs. Inner payloads (A response, b_result, data_result,
chart) stay dicts in this version; their fields are described in docs/contracts/."""

from .a import AAnswerRequest, ARequest, AResult, Decision, Option, PendingQuestion, TableRef
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
from .versions import CONTRACT_VERSION, PERIOD_CONDITION_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "PERIOD_CONDITION_VERSION",
    "AAnswerRequest",
    "A_HTTP_VERSION",
    "AHTTPAnswerRequest",
    "AHTTPReply",
    "AHTTPResult",
    "AHTTPSelectRequest",
    "ARequest",
    "AResult",
    "AppliedPeriodSources",
    "BRequest",
    "BResult",
    "BResultData",
    "BTableRef",
    "CalculationExtra",
    "CRequest",
    "Decision",
    "FetchRequest",
    "FetchResult",
    "Issue",
    "Meta",
    "MonthRange",
    "Option",
    "Outcome",
    "PendingQuestion",
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
