"""Reports counted from recorded history: counts only, never candidate data (week 4).

Import from the modules directly (reports.funnel, reports.timing).
"""


class ReportRefused(Exception):
    """The report cannot be run as asked. The message is safe to show."""
