"""Reusable scraper for Czech parliamentary election results with disk caching."""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests  # type: ignore[import-not-found]

DEFAULT_TIMEOUT = 15
DATA_BASE_URL_TEMPLATE = "https://www.volby.cz/appdata/ps{year}/"
APP_BASE_URL_TEMPLATE = "https://www.volby.cz/app/ps{year}/"
PRIMARY_RESOURCE = "vysled/celkem.json"
USER_AGENT = "VolbyCZ-Scraper/1.0 (+https://github.com/openai/codex-ci)"

CACHE_DIR = Path(__file__).resolve().parent.parent / "DATA"
CACHE_VERSION = 1
PARTIAL_REFRESH_INTERVAL = 60
FINAL_REFRESH_INTERVAL = 3600


class ElectionDataUnavailable(RuntimeError):
    """Raised when the requested election dataset is not yet published."""

    def __init__(
        self,
        year: int,
        resource: str,
        status: Optional[int] = None,
        reason: Optional[str] = None,
    ):
        detail = f"{resource}"
        if status is not None:
            detail += f" (status {status})"
        if reason:
            detail += f" · {reason.strip()}"
        super().__init__(
            f"Election results for {year} are not available yet or resource is unreachable: {detail}"
        )
        self.year = year
        self.resource = resource
        self.status = status
        self.reason = reason


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
                return script[start : index + 1]  # noqa: E203
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
        data_source: Optional[str] = None,
    ) -> None:
        self.year = year
        self.lang = lang
        self.timeout = timeout
        self.data_source = (data_source or "").strip("/")
        self._data_prefix = f"{self.data_source}/" if self.data_source else ""
        self.data_base_url = DATA_BASE_URL_TEMPLATE.format(year=year)
        self.app_base_url = APP_BASE_URL_TEMPLATE.format(year=year)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.resource_headers: Dict[str, Dict[str, str]] = {}
        self._national_data: Optional[Dict[str, Any]] = None
        self._party_lookup: Dict[int, str] = {}

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    def _normalized_resource(self, resource: str) -> str:
        return resource.lstrip("/")

    def _prefixed_resource(self, resource: str) -> str:
        normalized = self._normalized_resource(resource)
        return f"{self._data_prefix}{normalized}" if self._data_prefix else normalized

    def _build_data_url(self, resource: str) -> str:
        return urljoin(self.data_base_url, self._prefixed_resource(resource))

    def _build_app_url(self, resource: str) -> str:
        return urljoin(self.app_base_url, self._normalized_resource(resource))

    def _fetch_json(self, resource: str) -> Dict[str, Any]:
        prefixed_resource = self._prefixed_resource(resource)
        url = urljoin(self.data_base_url, prefixed_resource)
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:  # pragma: no cover - network failure
            raise ElectionDataUnavailable(self.year, prefixed_resource) from exc

        if response.status_code != 200:
            reason: Optional[str] = None
            content_type = response.headers.get("Content-Type", "")
            if "application/problem+json" in content_type:
                try:
                    problem = response.json()
                except ValueError:
                    problem = None
                if isinstance(problem, dict):
                    reason = str(
                        problem.get("message")
                        or problem.get("detail")
                        or problem.get("title")
                    )
            raise ElectionDataUnavailable(
                self.year, prefixed_resource, response.status_code, reason
            )

        self.resource_headers[prefixed_resource] = dict(response.headers)
        text = response.text
        lowered = text.lower()
        if "chyba 404" in lowered or "page not found" in lowered:
            raise ElectionDataUnavailable(
                self.year,
                prefixed_resource,
                response.status_code,
                "page not found",
            )

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON payload returned by {url}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected JSON structure returned by {url}")
        return payload

    def _get_national_data(self) -> Dict[str, Any]:
        if self._national_data is None:
            self._national_data = self._fetch_json(PRIMARY_RESOURCE)
        return self._national_data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_summary(self) -> Dict[str, Any]:
        """Return national level turnout and counting progress metrics."""
        data = self._get_national_data()
        row = data.get("prehled", [])
        if not isinstance(row, list) or len(row) < 9:
            raise RuntimeError("Unexpected JSON structure: summary data missing")

        def _get_number(values: List[Any], index: int) -> Optional[int]:
            if index >= len(values):
                return None
            return normalize_number(values[index])

        def _get_float(values: List[Any], index: int) -> Optional[float]:
            if index >= len(values):
                return None
            return normalize_percentage(values[index])

        summary: Dict[str, Any] = {
            "wards_total": _get_number(row, 0),
            "wards_processed": _get_number(row, 1),
            "wards_processed_percent": _get_float(row, 2),
            "voters_in_roll": _get_number(row, 3),
            "envelopes_issued": _get_number(row, 4),
            "turnout_percent": _get_float(row, 5),
            "envelopes_returned": _get_number(row, 8),
            "valid_votes": _get_number(row, 9),
            "valid_votes_percent": _get_float(row, 10),
        }

        envelopes_returned = summary["envelopes_returned"]
        valid_votes = summary["valid_votes"]
        if envelopes_returned is not None and valid_votes is not None:
            invalid = envelopes_returned - valid_votes
            summary["invalid_votes"] = invalid
            summary["invalid_votes_percent"] = (
                round(100.0 * invalid / envelopes_returned, 2)
                if envelopes_returned
                else None
            )
        else:
            summary["invalid_votes"] = None
            summary["invalid_votes_percent"] = None
        return summary

    def fetch_party_results(self) -> List[PartyResult]:
        data = self._get_national_data()
        entries = data.get("vysledky", [])
        if not isinstance(entries, list):
            return []

        results: List[PartyResult] = []
        self._party_lookup.clear()
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 4:
                continue
            party_number = normalize_number(entry[0])
            if party_number is None:
                continue
            party_name = str(entry[1])
            votes = normalize_number(entry[2]) or 0
            vote_share = normalize_percentage(entry[3]) or 0.0
            self._party_lookup[party_number] = party_name
            results.append(
                PartyResult(
                    number=party_number,
                    name=party_name,
                    votes=votes,
                    vote_share=vote_share,
                )
            )
        results.sort(key=lambda item: item.votes, reverse=True)
        return results

    def fetch_seat_allocation(self) -> List[SeatAllocation]:
        data = self._get_national_data()
        entries = data.get("vysledky", [])
        if not isinstance(entries, list):
            return []

        seat_results: List[SeatAllocation] = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 5:
                continue
            mandates = normalize_number(entry[4]) or 0
            if mandates <= 0:
                continue
            party_number = normalize_number(entry[0])
            party_name = self._party_lookup.get(
                party_number if party_number is not None else -1,
                str(entry[1]),
            )
            seat_results.append(
                SeatAllocation(
                    party=party_name,
                    mandates=mandates,
                    color=None,
                )
            )
        seat_results.sort(key=lambda seat: seat.mandates, reverse=True)
        return seat_results

    def fetch_region_leaders(self) -> List[RegionLeader]:
        if not self._party_lookup:
            self.fetch_party_results()

        payload = self._fetch_json("mapa_vitez.json")
        kraje = payload.get("kraje", {})
        if not isinstance(kraje, dict):
            return []

        leaders: List[RegionLeader] = []
        for region_id_str, raw in kraje.items():
            if not isinstance(raw, dict):
                continue
            try:
                region_id = int(region_id_str)
            except (TypeError, ValueError):
                continue
            party_number = normalize_number(raw.get("kstrana"))
            party_name = self._party_lookup.get(
                party_number if party_number is not None else -1,
                raw.get("kstranaZkratka", ""),
            )
            color_code = raw.get("kstranaBarva")
            color = (
                f"#{color_code}" if isinstance(color_code, str) and color_code else None
            )
            leaders.append(
                RegionLeader(
                    region_id=region_id,
                    region_name=str(raw.get("krajNazev", "")),
                    leading_party=party_name or "",
                    leading_percent=normalize_percentage(raw.get("procHlasu")),
                    votes=normalize_number(raw.get("hlasu")),
                    processed_percent=normalize_percentage(raw.get("procZprac")),
                    color=color,
                    detail_url=(
                        self._build_app_url(f"cs/results/{region_id}")
                        if region_id
                        else self._build_app_url("cs/results")
                    ),
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
        metadata: Dict[str, Any] = {
            "year": self.year,
            "lang": self.lang,
            "fetched_at": now,
            "source": self._build_data_url(PRIMARY_RESOURCE),
            "resource_headers": copy.deepcopy(self.resource_headers),
        }
        if self.data_source:
            metadata["data_source"] = self.data_source
        return {
            "metadata": metadata,
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
    url = DATA_BASE_URL_TEMPLATE.format(year=year) + _primary_resource(lang)
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
        scraper.resource_headers[_primary_resource(lang)] = dict(
            prefetched_response.headers
        )
    return scraper.fetch_all()
