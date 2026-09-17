from core.validate import validate_app_name


def test_valid_names():
    assert validate_app_name("instagram") is True
    assert validate_app_name("speedtest") is True
    assert validate_app_name("youtube-music") is True
