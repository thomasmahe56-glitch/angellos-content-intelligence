"""Angellos Content Intelligence V2 public API."""

async def run_daily_scout(*args, **kwargs):
    """Lazy public import so lightweight utilities work without browser extras."""
    from .runner import run_daily_scout as _run_daily_scout
    return await _run_daily_scout(*args, **kwargs)


__all__ = ["run_daily_scout"]
