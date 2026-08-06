"""Grade institutions on what they disclose, not on how they perform."""

from .disclosure import Disclosure, classify
from .grading import InstitutionGrade, grade_institution, summarize

__all__ = ["Disclosure", "InstitutionGrade", "classify", "grade_institution", "summarize"]
__version__ = "0.1.0.dev0"
