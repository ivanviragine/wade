"""Task backend providers — ABC and implementations."""

from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider

__all__ = ["AbstractTaskProvider", "get_provider"]
