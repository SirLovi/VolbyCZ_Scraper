"""Reusable scraper for Czech parliamentary election results with disk caching."""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, asdict, is_dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import pandas as pd
import requests  # type: ignore[import-not-found]

DEFAULT_TIMEOUT = 15
BASE_URL_TEMPLATE = "https://www.volby.cz/pls/ps{year}/"
USER_AGENT = "VolbyCZ-Scraper/1.0 (+https://github.com/openai/codex-ci)"

CACHE_DIR = Path(__file__).resolve().parent.parent / "DATA"
CACHE_VERSION = 1
PARTIAL_REFRESH_INTERVAL = 60
FINAL_REFRESH_INTERVAL = 3600


class ElectionDataUnavailable(RuntimeError):
    """Raised when the requested election dataset is not yet published."""

    def __init__(self, year: int, resource: str, status: Optional[int] = None):
        detail = f"{resource}"
        if status is not None:
            detail += f" (status {status})"
        super().__init__(
            f"Election results for {year} are not available yet or resource is unreachable: {detail}"
        )
        self.year = year
        self.resource = resource
        self.status = status


_number_pattern = re.compile(r"[^0-9-]")
_percent_pattern = re.compile(r"-?[0-9]+(?:[\.,][0-9]+)?")


def _extract_js_literal(script: str, marker: str, opening: str) -> str:
    start = script.find(marker)
    if start == -1:
        raise ValueError(f"Could not locate marker {marker!r}")
    start = script.find(opening, start)
    if start == -1:
        raise ValueError(
            f"Could not locate opening {opening!r} after marker {marker!r}"
        )
    closing = "]" if opening == "[" else "}"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(script)):
        char = script[index]
        if char == "\\" and not escape:
            escape = True
            continue
        if char in "'\"" and not escape:
            in_string = not in_string
        if in_string:
            escape = False
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return script[start : index + 1]  # flake8: ignore[E203]
        escape = False
    raise ValueError("Unbalanced braces while parsing JS literal")


def _js_object_to_json(value: str) -> str:
    sanitized = re.sub(r"(\w+):", lambda match: f'"{match.group(1)}":', value)
    return sanitized.replace("'", '"')


def normalize_number(value: Any) -> Optional[int]:
    """Convert a numeric string with non-breaking spaces to int."""

    if value is None:
        return None
    if isinstance(value, (int,)) and not isinstance(value, bool):  # type: ignore[unreachable]
        return int(value)
    if isinstance(value, float) and not value.is_integer():
        return int(round(value))
    text = str(value).strip()
    if not text or text == "-":
        return None
    digits = _number_pattern.sub("", text)
    if not digits:
        return None
    return int(digits)


def normalize_percentage(value: Any) -> Optional[float]:
    """Extract percentage value as float from a formatted string."""

    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _percent_pattern.search(str(value).replace(",", "."))
    return float(match.group()) if match else None


@dataclass
class PartyResult:
    number: int
    name: str
    votes: int
    vote_share: float


@dataclass
class SeatAllocation:
    party: str
    mandates: int
    color: Optional[str]


@dataclass
class RegionLeader:
    region_id: int
    region_name: str
    leading_party: str
    leading_percent: Optional[float]
    votes: Optional[int]
    processed_percent: Optional[float]
    color: Optional[str]
    detail_url: str


class ElectionScraper:
    """Scraper for Czech parliamentary election results hosted on volby.cz."""

    def __init__(
        self,
        year: int = 2025,
        lang: str = "EN",
        session: Optional[requests.Session] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.year = year
        self.lang = lang
        self.timeout = timeout
        self.base_url = BASE_URL_TEMPLATE.format(year=year)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._ps2_tables: Optional[List[pd.DataFrame]] = None
        self.resource_headers: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    def _build_url(self, resource: str) -> str:
        return urljoin(self.base_url, resource)

    def _fetch_text(self, resource: str) -> str:
        url = self._build_url(resource)
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code != 200:
            raise ElectionDataUnavailable(self.year, resource, response.status_code)
        self.resource_headers[resource] = dict(response.headers)
        text = response.text
        lowered = text.lower()
        if "chyba 404" in lowered or "page not found" in lowered:
            raise ElectionDataUnavailable(self.year, resource, response.status_code)
        return text

    def _fetch_tables(self, resource: str) -> List[pd.DataFrame]:
        html = self._fetch_text(resource)
        return pd.read_html(StringIO(html))

    def _ps2_tables_cached(self) -> List[pd.DataFrame]:
        if self._ps2_tables is None:
            self._ps2_tables = self._fetch_tables(f"ps2?xjazyk={self.lang}")
        return self._ps2_tables

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_summary(self) -> Dict[str, Any]:
        """Return national level turnout and counting progress metrics."""

        tables = self._ps2_tables_cached()
        if not tables:
            raise RuntimeError("Unexpected HTML structure: summary table missing")
        summary_table = tables[0]
        if not isinstance(summary_table.columns, pd.MultiIndex):
            raise ElectionDataUnavailable(
                self.year,
                "ps2 summary table",
            )
        summary_row = summary_table.iloc[0]
        summary = {
            "wards_total": normalize_number(summary_row[("Wards", "total")]),
            "wards_processed": normalize_number(summary_row[("Wards", "proc.")]),
            "wards_processed_percent": normalize_percentage(
                summary_row[("Wards", "in %")]
            ),
            "voters_in_roll": normalize_number(
                summary_row[
                    ("Voters in the electoral roll", "Voters in the electoral roll")
                ]
            ),
            "envelopes_issued": normalize_number(
                summary_row[("Issued envelopes", "Issued envelopes")]
            ),
            "turnout_percent": normalize_percentage(
                summary_row[("Turnout in %", "Turnout in %")]
            ),
            "envelopes_returned": normalize_number(
                summary_row[("Returned envelopes", "Returned envelopes")]
            ),
            "valid_votes": normalize_number(
                summary_row[("Valid votes", "Valid votes")]
            ),
            "valid_votes_percent": normalize_percentage(
                summary_row[("% of valid votes", "% of valid votes")]
            ),
        }
        if (
            summary["envelopes_returned"] is not None
            and summary["valid_votes"] is not None
        ):
            invalid = summary["envelopes_returned"] - summary["valid_votes"]
            summary["invalid_votes"] = invalid
            summary["invalid_votes_percent"] = (
                round(100.0 * invalid / summary["envelopes_returned"], 2)
                if summary["envelopes_returned"]
                else None
            )
        else:
            summary["invalid_votes"] = None
            summary["invalid_votes_percent"] = None
        return summary

    def fetch_party_results(self) -> List[PartyResult]:
        tables = self._ps2_tables_cached()
        if len(tables) < 2:
            raise RuntimeError("Unexpected HTML structure: party tables missing")
        party_frames: List[pd.DataFrame] = []
        for table in tables[1:]:
            table.columns = [
                "party_number",
                "party_name",
                "votes_total",
                "votes_percent",
            ]
            party_frames.append(table)
        combined = pd.concat(party_frames, ignore_index=True)
        combined["party_number"] = combined["party_number"].apply(normalize_number)
        combined["votes_total"] = combined["votes_total"].apply(normalize_number)
        combined["votes_percent"] = combined["votes_percent"].apply(
            normalize_percentage
        )
        records: List[PartyResult] = []
        for row in combined.to_dict(orient="records"):
            records.append(
                PartyResult(
                    number=row["party_number"],
                    name=row["party_name"],
                    votes=row["votes_total"],
                    vote_share=row["votes_percent"],
                )
            )
        records.sort(
            key=lambda item: (item.votes if item.votes is not None else 0), reverse=True
        )
        return records

    def fetch_seat_allocation(self) -> List[SeatAllocation]:
        script = self._fetch_text(f"d3_rects?xjazyk={self.lang}")
        try:
            literal = _extract_js_literal(script, "let data", "[")
        except ValueError:
            return []
        payload = _js_object_to_json(literal)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        seat_results: List[SeatAllocation] = []
        for item in data:
            seat_results.append(
                SeatAllocation(
                    party=item.get("party", ""),
                    mandates=int(normalize_number(item.get("mandate")) or 0),
                    color=item.get("color"),
                )
            )
        seat_results.sort(key=lambda s: s.mandates, reverse=True)
        return seat_results

    def fetch_region_leaders(self) -> List[RegionLeader]:
        script = self._fetch_text(f"d3_mapa?xjazyk={self.lang}")
        try:
            literal = _extract_js_literal(script, "let data", "{")
        except ValueError:
            return []
        payload = _js_object_to_json(literal)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        leaders: List[RegionLeader] = []
        for region_id, raw in data.items():
            processed_percent = normalize_percentage(raw.get("processed"))
            votes = normalize_number(raw.get("votes"))
            leaders.append(
                RegionLeader(
                    region_id=int(region_id),
                    region_name=raw.get("region", ""),
                    leading_party=raw.get("party", ""),
                    leading_percent=normalize_percentage(raw.get("percent")),
                    votes=votes,
                    processed_percent=processed_percent,
                    color=raw.get("color"),
                    detail_url=self._build_url(raw.get("link", "")),
                )
            )
        leaders.sort(key=lambda item: item.region_id)
        return leaders

    def fetch_all(self) -> Dict[str, Any]:
        summary = self.fetch_summary()
        parties = self.fetch_party_results()
        seats = self.fetch_seat_allocation()
        regions = self.fetch_region_leaders()
        now = time.time()
        return {
            "metadata": {
                "year": self.year,
                "lang": self.lang,
                "fetched_at": now,
                "source": self._build_url(f"ps2?xjazyk={self.lang}"),
                "resource_headers": copy.deepcopy(self.resource_headers),
            },
            "summary": summary,
            "parties": parties,
            "seats": seats,
            "regions": regions,
        }


def gather_election_data(
    year: int = 2025,
    fallback_year: Optional[int] = 2021,
    lang: str = "EN",
) -> Dict[str, Any]:
    """Fetch election data with persistent caching and optional fallback."""

    lang = lang.upper()

    try:
        dataset = _get_dataset_with_cache(year=year, lang=lang)
    except ElectionDataUnavailable as exc:
        if fallback_year is None or fallback_year == year:
            raise exc
        fallback_dataset = _get_dataset_with_cache(year=fallback_year, lang=lang)
        fallback_metadata = fallback_dataset.setdefault("metadata", {})
        fallback_metadata["effective_year"] = fallback_year
        fallback_metadata["requested_year"] = year
        fallback_metadata["fallback_used"] = True
        return fallback_dataset

    metadata = dataset.setdefault("metadata", {})
    metadata["effective_year"] = year
    metadata["requested_year"] = year
    metadata["fallback_used"] = False
    return dataset


# ---------------------------------------------------------------------------
# Disk caching utilities
# ---------------------------------------------------------------------------


def _cache_path(year: int, lang: str) -> Path:
    return CACHE_DIR / f"ps{year}_{lang.lower()}.json"


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _convert_serializable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list):
        return [_convert_serializable(item) for item in value]
    if isinstance(value, dict):
        return {key: _convert_serializable(item) for key, item in value.items()}
    return value


def _serialize_dataset(dataset: Dict[str, Any]) -> Dict[str, Any]:
    return _convert_serializable(dataset)


def _deserialize_dataset(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = copy.deepcopy(payload)
    data["parties"] = [PartyResult(**item) for item in data.get("parties", [])]
    data["seats"] = [SeatAllocation(**item) for item in data.get("seats", [])]
    data["regions"] = [RegionLeader(**item) for item in data.get("regions", [])]
    return data


def _load_cache(year: int, lang: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(year, lang)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        return None
    if payload.get("version") != CACHE_VERSION:
        return None
    dataset = _deserialize_dataset(payload.get("data", {}))
    return {
        "path": path,
        "etag": payload.get("etag"),
        "checked_at": payload.get("checked_at", 0.0),
        "data": dataset,
    }


def _store_cache(
    year: int,
    lang: str,
    dataset: Dict[str, Any],
    etag: Optional[str],
    checked_at: Optional[float] = None,
) -> None:
    _ensure_cache_dir()
    snapshot = copy.deepcopy(dataset)
    snapshot.get("metadata", {}).pop("cache", None)
    payload = {
        "version": CACHE_VERSION,
        "etag": etag,
        "checked_at": checked_at or time.time(),
        "data": _serialize_dataset(snapshot),
    }
    with _cache_path(year, lang).open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _refresh_interval(summary: Dict[str, Any]) -> int:
    processed_pct = summary.get("wards_processed_percent")
    if isinstance(processed_pct, (int, float)) and processed_pct >= 100:
        return FINAL_REFRESH_INTERVAL
    return PARTIAL_REFRESH_INTERVAL


def _should_revalidate(cache_entry: Dict[str, Any]) -> bool:
    summary = {}
    if isinstance(cache_entry.get("data"), dict):
        summary = cache_entry["data"].get("summary", {})
    interval = _refresh_interval(summary)
    checked_at = cache_entry.get("checked_at", 0.0)
    return (time.time() - checked_at) >= interval


def _primary_resource(lang: str) -> str:
    return f"ps2?xjazyk={lang}"


def _extract_primary_etag(dataset: Dict[str, Any], lang: str) -> Optional[str]:
    metadata = dataset.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    headers_map = metadata.get("resource_headers", {})
    if not isinstance(headers_map, dict):
        return None
    resource_headers = headers_map.get(_primary_resource(lang), {})
    if isinstance(resource_headers, dict):
        return resource_headers.get("ETag") or resource_headers.get("etag")
    return None


def _annotate_cache_metadata(
    dataset: Dict[str, Any],
    *,
    year: int,
    lang: str,
    cache_hit: bool,
    revalidated: bool,
    cache_entry: Optional[Dict[str, Any]],
) -> None:
    metadata = dataset.setdefault("metadata", {})
    cache_info = {
        "hit": cache_hit,
        "revalidated": revalidated,
        "path": str(_cache_path(year, lang)),
    }
    if cache_entry:
        cache_info["checked_at"] = cache_entry.get("checked_at")
        cache_info["etag"] = cache_entry.get("etag")
    metadata["cache"] = cache_info


def _get_dataset_with_cache(year: int, lang: str) -> Dict[str, Any]:
    cache_entry = _load_cache(year, lang)

    if cache_entry and not _should_revalidate(cache_entry):
        dataset = copy.deepcopy(cache_entry["data"])
        _annotate_cache_metadata(
            dataset,
            year=year,
            lang=lang,
            cache_hit=True,
            revalidated=False,
            cache_entry=cache_entry,
        )
        return dataset

    etag = cache_entry.get("etag") if cache_entry else None
    if cache_entry and etag:
        changed, prefetched, new_etag = _revalidate_primary_resource(year, lang, etag)
        if not changed:
            dataset = copy.deepcopy(cache_entry["data"])
            cache_entry["checked_at"] = time.time()
            _store_cache(year, lang, dataset, etag, cache_entry["checked_at"])
            _annotate_cache_metadata(
                dataset,
                year=year,
                lang=lang,
                cache_hit=True,
                revalidated=True,
                cache_entry=cache_entry,
            )
            return dataset
        dataset = _fetch_dataset(year, lang, prefetched)
        resulting_etag = new_etag or _extract_primary_etag(dataset, lang)
        checked_at = time.time()
        _store_cache(year, lang, dataset, resulting_etag, checked_at)
        _annotate_cache_metadata(
            dataset,
            year=year,
            lang=lang,
            cache_hit=False,
            revalidated=True,
            cache_entry={"etag": resulting_etag, "checked_at": checked_at},
        )
        return dataset

    dataset = _fetch_dataset(year, lang, None)
    resulting_etag = _extract_primary_etag(dataset, lang)
    checked_at = time.time()
    _store_cache(year, lang, dataset, resulting_etag, checked_at)
    _annotate_cache_metadata(
        dataset,
        year=year,
        lang=lang,
        cache_hit=False,
        revalidated=False,
        cache_entry={"etag": resulting_etag, "checked_at": checked_at},
    )
    return dataset


def _revalidate_primary_resource(
    year: int, lang: str, etag: str
) -> tuple[bool, Optional[requests.Response], Optional[str]]:
    url = BASE_URL_TEMPLATE.format(year=year) + _primary_resource(lang)
    try:
        response = requests.get(
            url, headers={"If-None-Match": etag}, timeout=DEFAULT_TIMEOUT
        )
    except requests.RequestException as exc:
        raise ElectionDataUnavailable(year, _primary_resource(lang)) from exc
    if response.status_code == 304:
        return False, None, etag
    if response.status_code == 200:
        return (
            True,
            response,
            response.headers.get("ETag") or response.headers.get("etag"),
        )
    raise ElectionDataUnavailable(year, _primary_resource(lang), response.status_code)


def _fetch_dataset(
    year: int,
    lang: str,
    prefetched_response: Optional[requests.Response],
) -> Dict[str, Any]:
    scraper = ElectionScraper(year=year, lang=lang)
    if prefetched_response is not None:
        scraper._ps2_tables = pd.read_html(StringIO(prefetched_response.text))
        scraper.resource_headers[_primary_resource(lang)] = dict(
            prefetched_response.headers
        )
    return scraper.fetch_all()
