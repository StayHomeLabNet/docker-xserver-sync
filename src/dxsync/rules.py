from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from .models import normalize_relative_path


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/").lstrip("/")


def _matches(path: str, pattern: str, *, is_directory: bool) -> bool:
    pattern = _normalize_pattern(pattern)
    if not pattern or pattern.startswith("#"):
        return False

    directory_rule = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    if not pattern:
        return False

    if directory_rule:
        parts = path.split("/")
        candidates = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
        if "/" not in pattern:
            return any(fnmatchcase(candidate.split("/")[-1], pattern) for candidate in candidates)
        return any(fnmatchcase(candidate, pattern) for candidate in candidates)

    if "/" not in pattern:
        return any(fnmatchcase(part, pattern) for part in path.split("/"))

    if fnmatchcase(path, pattern):
        return True
    return is_directory and fnmatchcase(path + "/", pattern)


@dataclass(frozen=True)
class PathRules:
    patterns: tuple[str, ...]

    def matches(self, relative_path: str, *, is_directory: bool = False) -> bool:
        path = normalize_relative_path(relative_path)
        matched = False
        for raw_pattern in self.patterns:
            pattern = raw_pattern.strip()
            if not pattern or pattern.startswith("#"):
                continue
            negated = pattern.startswith("!")
            if negated:
                pattern = pattern[1:]
            if _matches(path, pattern, is_directory=is_directory):
                matched = not negated
        return matched

