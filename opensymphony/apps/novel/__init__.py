"""Novel writing application — InkOS精华移植到Symphony."""

from .anti_ai import AntiAI, AntiAIResult
from .auditor import DIMENSIONS, AuditCategory, AuditIssue, AuditResult, NovelAuditor, Severity
from .observer import Fact, FactCategory, Observer
from .reflector import ReflectionResult, Reflector
from .style_imitator import StyleFingerprint, StyleImitator
from .templates import NOVEL_PIPELINE_STEPS, NovelPipeline, NovelPipelineResult
from .truth_files import TruthFile, TruthFiles

__all__ = [
    "TruthFiles", "TruthFile",
    "Observer", "Fact", "FactCategory",
    "Reflector", "ReflectionResult",
    "NovelAuditor", "AuditResult", "AuditIssue", "Severity", "AuditCategory", "DIMENSIONS",
    "AntiAI", "AntiAIResult",
    "StyleImitator", "StyleFingerprint",
    "NovelPipeline", "NovelPipelineResult", "NOVEL_PIPELINE_STEPS",
]
