from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

import certifi


GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/StayHomeLabNet/docker-xserver-sync/releases/latest"
)
GITHUB_RELEASES_PAGE = (
    "https://github.com/StayHomeLabNet/docker-xserver-sync/releases"
)

_SEMVER_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$")


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    update_available: bool
    release_url: str
    release_name: str = ""
    published_at: str = ""
    notes_excerpt: str = ""
    recommended_asset_name: str = ""
    recommended_asset_url: str = ""


def select_recommended_asset(
    assets: object,
    *,
    platform: str | None = None,
) -> tuple[str, str]:
    if not isinstance(assets, list):
        return "", ""
    target_platform = platform or sys.platform
    valid_assets: list[tuple[str, str]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        if name and url.startswith("https://"):
            valid_assets.append((name, url))

    if target_platform == "win32":
        patterns = ("setup", ".exe", "windows")
    elif target_platform == "darwin":
        patterns = ("macos", ".zip", ".dmg")
    else:
        patterns = ("linux", ".tar.gz")

    for pattern in patterns:
        for name, url in valid_assets:
            if pattern in name.lower():
                return name, url
    return "", ""


def parse_semver(version: str) -> tuple[tuple[int, ...], int, str]:
    cleaned = version.strip()
    match = _SEMVER_RE.match(cleaned)
    if not match:
        raise UpdateCheckError(f"バージョン表記を解釈できません: {version!r}")
    core_parts = tuple(int(part) for part in match.group(1).split("."))
    while len(core_parts) < 3:
        core_parts = (*core_parts, 0)
    prerelease = match.group(2) or ""
    stability = 0 if prerelease else 1
    return core_parts, stability, prerelease


def is_newer_version(current_version: str, latest_version: str) -> bool:
    return parse_semver(latest_version) > parse_semver(current_version)


def check_for_updates(
    current_version: str,
    *,
    api_url: str = GITHUB_LATEST_RELEASE_API,
    timeout: float = 10.0,
    urlopen_func: Callable[..., object] | None = None,
) -> UpdateCheckResult:
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Docker-XServer-Sync/{current_version}",
        },
    )
    try:
        if urlopen_func is None:
            context = ssl.create_default_context(cafile=certifi.where())
            response_context = urllib.request.urlopen(
                request,
                timeout=timeout,
                context=context,
            )
        else:
            response_context = urlopen_func(request, timeout=timeout)
        with response_context as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise UpdateCheckError(
            f"リリース情報の取得に失敗しました (HTTP {exc.code})。"
        ) from exc
    except Exception as exc:
        raise UpdateCheckError(
            f"リリース情報の取得に失敗しました: {exc}"
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateCheckError("リリース情報のJSONを解析できませんでした。") from exc

    if not isinstance(payload, dict):
        raise UpdateCheckError("リリース情報の形式が不正です。")

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateCheckError("最新バージョンのタグ情報が見つかりませんでした。")

    latest_version = tag_name.lstrip("vV")
    update_available = is_newer_version(current_version, latest_version)
    release_url = str(payload.get("html_url") or "").strip()
    if not release_url.startswith("https://"):
        release_url = GITHUB_RELEASES_PAGE
    release_name = str(payload.get("name") or tag_name).strip()
    published_at = str(payload.get("published_at") or "").strip()
    body = str(payload.get("body") or "").strip()
    notes_excerpt = body[:280].rstrip() + ("…" if len(body) > 280 else "")
    asset_name, asset_url = select_recommended_asset(payload.get("assets"))

    return UpdateCheckResult(
        current_version=current_version.lstrip("vV"),
        latest_version=latest_version,
        update_available=update_available,
        release_url=release_url,
        release_name=release_name,
        published_at=published_at,
        notes_excerpt=notes_excerpt,
        recommended_asset_name=asset_name,
        recommended_asset_url=asset_url,
    )
