"""Girdi doğrulama fonksiyonları."""

def validate_app_name(name: str) -> bool:
    return name.replace("-", "").isalnum()
