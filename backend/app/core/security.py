from sdk.auth.api_key import make_api_key_dependency
from sdk.ratelimit import RateLimiter

from app.core.config import get_settings

# API-key dependency bound to this app's settings (see sdk.auth.api_key).
require_api_key = make_api_key_dependency(lambda: get_settings().api_key.get_secret_value())

# Limiter bound to RATE_LIMIT_PER_MINUTE (see sdk.ratelimit).
rate_limit = RateLimiter(lambda: get_settings().rate_limit_per_minute)


def reset_rate_limiter() -> None:
    """Clear all counters, including failed-auth counters (used by tests)."""
    rate_limit.reset()
    require_api_key.reset_failure_limiter()
