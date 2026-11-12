"""
errors.py
Exception hierarchy for the garden domain model and the training pipeline.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations


class GardenError(Exception):
    """Base class for every error raised by the garden packages."""


class ValidationError(GardenError, ValueError):
    """A domain object was built with physically impossible values."""

    def __init__(self, owner: str, message: str):
        super().__init__(f"{owner}: {message}")
        self.owner = owner


class CatalogueError(GardenError, KeyError):
    """A soil or plant key is not in its catalogue."""

    def __init__(self, catalogue: str, key: str, known: list[str]):
        super().__init__(f"unknown {catalogue} {key!r}; known: {', '.join(sorted(known))}")
        self.catalogue, self.key = catalogue, key

    def __str__(self) -> str:  # KeyError would otherwise wrap the message in quotes
        return self.args[0]


class ConfigError(GardenError):
    """The garden configuration file is malformed or inconsistent."""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path


class DataError(GardenError):
    """An input table is missing columns or has no usable rows."""
