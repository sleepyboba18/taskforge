"""Pure retry eligibility and exponential backoff policy."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.task_executor import NonRetryableTaskError


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Decision produced for one failed execution."""

    should_retry: bool
    retry_number: int | None = None
    delay_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Calculate capped exponential delays without owning persistence."""

    base_delay: float = 5.0
    max_delay: float = 3600.0

    def __post_init__(self) -> None:
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("Retry delays cannot be negative.")
        if self.max_delay < self.base_delay:
            raise ValueError("Maximum retry delay must be at least the base delay.")

    def delay_for(self, retry_number: int) -> float:
        """Return the capped delay for a one-based retry number."""
        if retry_number < 1:
            raise ValueError("Retry number must be at least 1.")
        return min(self.max_delay, self.base_delay * (2 ** (retry_number - 1)))

    def decide(self, *, retry_count: int, max_retries: int, error: Exception) -> RetryDecision:
        """Determine whether a failed attempt may schedule another retry."""
        next_retry_number = retry_count + 1
        if isinstance(error, NonRetryableTaskError) or next_retry_number > max_retries:
            return RetryDecision(should_retry=False)
        return RetryDecision(
            should_retry=True,
            retry_number=next_retry_number,
            delay_seconds=self.delay_for(next_retry_number),
        )
