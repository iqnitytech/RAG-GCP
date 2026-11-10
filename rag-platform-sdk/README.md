# rag-platform-sdk

Shared FastAPI / GCP building blocks used by the RAG services: GCP + API-key
auth, generic schemas, structured logging with request ids, middleware, error
handling, rate limiting, health probes and `create_base_app`.

Never imports from the consuming application; apps inject their settings.

## Develop
```powershell
pip install -e ".[dev]"
python -m pytest
```

## Release
1. Bump `sdk/_version.py` (semver) and add a `CHANGELOG.md` entry.
2. `python -m pytest`
3. Build the wheel and hand it to a consumer:
   ```powershell
   .\scripts\release.ps1                      # builds dist\*.whl and copies it to ..\backend\vendor
   ```
   For a registry instead: `twine upload --repository-url https://asia-south1-python.pkg.dev/<PROJECT>/<REPO>/ dist/*`

## Consume
The consumer lists a version range in `requirements.txt` and resolves it from
its `vendor/` folder (or a registry):
```
--find-links ./vendor
rag-platform-sdk>=0.2,<0.3
```
Breaking change => bump MAJOR (or MINOR while 0.x) and raise the range in the
consumer on purpose.

