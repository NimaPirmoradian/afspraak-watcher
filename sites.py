"""
Loads site definitions from sites/*.yaml.

A site YAML is a PURE site definition: how to navigate the page and how to
extract the date. User-controllable knobs (active flag, deadline, etc.) live
in state files, not here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent
SITES_DIR = ROOT / "sites"

log = logging.getLogger("sites")


@dataclass
class SiteConfig:
    site_id: str          # filename stem, e.g. "ridderkerk"
    name: str             # display name, e.g. "Ridderkerk Rijbewijs"
    url: str
    steps: list[dict[str, Any]]
    extract: list[dict[str, Any]]
    max_retries: int = 3

    @classmethod
    def from_yaml(cls, path: Path) -> "SiteConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            site_id=path.stem,
            name=str(data["name"]),
            url=str(data["url"]),
            steps=list(data["steps"]),
            extract=list(data["extract"]),
            max_retries=int(data.get("max_retries", 3)),
        )


def list_sites() -> dict[str, SiteConfig]:
    """Return all sites keyed by site_id. Bad YAMLs are logged and skipped."""
    sites: dict[str, SiteConfig] = {}
    for path in sorted(list(SITES_DIR.glob("*.yaml")) + list(SITES_DIR.glob("*.yml"))):
        try:
            cfg = SiteConfig.from_yaml(path)
            sites[cfg.site_id] = cfg
        except Exception as e:
            log.error(f"Failed to load {path.name}: {e}")
    return sites


def load_site(site_id: str) -> SiteConfig | None:
    return list_sites().get(site_id)
