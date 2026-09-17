"""Yeniden deneme dekoratörü."""

import time
from functools import wraps


def retry(attempts=3, delay=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(attempts):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if i == attempts - 1:
                        raise
                    time.sleep(delay)

        return wrapper

    return decorator
