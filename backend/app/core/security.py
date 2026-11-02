from sdk.auth.api_key import make_api_key_dependency

from app.core.config import get_settings

# API-key dependency bound to this app's settings (see sdk.auth.api_key).
require_api_key = make_api_key_dependency(lambda: get_settings().api_key.get_secret_value())
