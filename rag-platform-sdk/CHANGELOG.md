# SDK changelog

Semantic versioning: MAJOR = breaking change, MINOR = backwards-compatible
feature, PATCH = fix. While 0.x, a MINOR bump may be breaking. Apps pin a
range and guard it with `require_sdk_version(minimum=..., below=...)`.

## 0.2.0 - 2026-11-10
- Added Generic Enterprise Data Hub (EDH) Connector under `sdk.connectors.edh` and organized SDK models under `sdk.models.edh_models` and `sdk.models.service_models`. Uses the Strategy Pattern, SOLID role-segregated ports (`EdhReadStrategyPort`, `EdhWriteStrategyPort`, `EdhRecordStrategyPort`, `EdhLifecyclePort`), explicit allowlist `EdhStrategyRegistry`, thread-safe `CircuitBreaker` + retry with jitter, and six core-library strategies (`DataLakeStrategy`, `ObjectStorageHubStrategy`, `ApiDrivenEdhStrategy`, `SalesforceEdhStrategy` + `make_salesforce_oauth_token_getter`, `DatabricksEdhStrategy` + `make_databricks_oauth_token_getter`, and `InMemoryEdhStrategy`).
- `make_api_key_dependency` now rate-limits failed authentication attempts per client IP before comparing the secret key (SEC-1).
- `RateLimiter` evicts expired entries and bounds tracked identities via `max_identities` LRU eviction to prevent unbounded memory growth (SEC-1).
- Added `BodySizeLimitMiddleware` (and `create_base_app` parameters) to enforce declared `Content-Length` and actual streamed byte limits before multipart/body parsing (SEC-2).

## 0.1.0 - 2026-11-02
- Initial release: GCP auth (WIF / local / impersonation), API-key auth,
  generic schemas (`ApiResponse`, `Page`, `ErrorResponse`, `PageParams`),
  structured logging with request ids, request-context and security-header
  middleware, `AppError` hierarchy and handlers, rate limiter, health probes,
  and `create_base_app`.

