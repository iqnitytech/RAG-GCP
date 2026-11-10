import pytest

import sdk
from sdk.version import SdkVersionError, parse_version, require_sdk_version


def test_version_exposed_and_semver():
    assert sdk.__version__ == "0.2.0"
    assert parse_version(sdk.__version__) == (0, 2, 0)


@pytest.mark.parametrize("value,expected", [("1.2", (1, 2, 0)), ("2.0.3-rc1", (2, 0, 3)), ("1.0.0+b5", (1, 0, 0))])
def test_parse_version(value, expected):
    assert parse_version(value) == expected


def test_parse_version_rejects_garbage():
    with pytest.raises(ValueError):
        parse_version("abc")


def test_require_sdk_version_range():
    assert require_sdk_version(minimum="0.2.0", below="0.3.0") == sdk.__version__
    with pytest.raises(SdkVersionError):
        require_sdk_version(minimum="0.3.0", below="0.4.0")
    with pytest.raises(SdkVersionError):
        require_sdk_version(minimum="0.1.0", below="0.2.0")

