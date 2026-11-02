from sdk.ratelimit import RateLimiter

from app.core.config import get_settings

# Limiter bound to RATE_LIMIT_PER_MINUTE (see sdk.ratelimit).
rate_limit = RateLimiter(lambda: get_settings().rate_limit_per_minute)


def reset_rate_limiter() -> None:
    """Clear all counters (used by tests)."""
    rate_limit.reset()
