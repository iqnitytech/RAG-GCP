"""Version parsing and compatibility guard for applications using the SDK."""

from sdk._version import __version__


class SdkVersionError(RuntimeError):
    """Raised when the installed SDK does not satisfy the app's requirement."""


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` (pre-release/build suffixes are ignored)."""
    core = value.strip().split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Invalid version: {value!r}")
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return nums[0], nums[1], nums[2]


def require_sdk_version(*, minimum: str, below: str) -> str:
    """Fail fast unless ``minimum <= installed < below``.

    Typical use at app start-up (compatible-release style)::

        require_sdk_version(minimum="0.1.0", below="0.2.0")

    Returns the installed version.
    """
    installed = parse_version(__version__)
    if not (parse_version(minimum) <= installed < parse_version(below)):
        raise SdkVersionError(
            f"Incompatible SDK: app requires >={minimum},<{below} "
            f"but sdk=={__version__} is installed."
        )
    return __version__

