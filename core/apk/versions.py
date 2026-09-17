"""Sürüm kıyaslama ve dize formatlama yardımcıları."""

def compare_versions(v1: str, v2: str) -> int:
    parts1 = [int(x) for x in v1.split(".") if x.isdigit()]
    parts2 = [int(x) for x in v2.split(".") if x.isdigit()]
    return (parts1 > parts2) - (parts1 < parts2)
