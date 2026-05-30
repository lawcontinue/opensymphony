"""Novel writing application — InkOS精华移植到Symphony."""

from .novel.auditor import AuditIssue, AuditResult, NovelAuditor
from .novel.observer import Fact, Observer
from .novel.truth_files import TruthFile, TruthFiles

__all__ = [
    "TruthFiles", "TruthFile",
    "Observer", "Fact",
    "NovelAuditor", "AuditResult", "AuditIssue",
]
