"""Soyut kaynak sağlayıcı sözleşmesi."""

from abc import ABC, abstractmethod


class BaseSourceProvider(ABC):
    @abstractmethod
    def get_apk(self, app_key: str, destination_path: str) -> str:
        pass
