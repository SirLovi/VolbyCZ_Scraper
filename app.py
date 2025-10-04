"""Streamlit dashboard for Czech Parliament election night coverage."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast
from urllib.parse import urlencode

import altair as alt
import pandas as pd
import streamlit as st
import pydeck as pdk

from volbycz_scraper import ElectionDataUnavailable, gather_election_data

st.set_page_config(
    page_title="Czech Parliamentary Elections 2025 Dashboard",
    layout="wide",
    page_icon="🗳️",
)

PRIMARY_YEAR = 2025
FALLBACK_YEAR = 2021
BASELINE_YEAR = 2021
SEAT_TARGET = 200
MAJORITY_THRESHOLD = 101
CACHE_TTL_SECONDS = 5 * 60

LANGUAGE_OPTIONS = {"Čeština": "cs", "English": "en"}

DEFAULT_PARTY_COLORS = [
    "#3778c2",
    "#e4572e",
    "#4c9f70",
    "#f2b134",
    "#a1c181",
    "#e05d5d",
    "#6f4c9b",
    "#17a2b8",
    "#d17b0f",
    "#b3b3b3",
]

REGION_COORDINATES: Dict[int, Dict[str, float]] = {
    1: {"lat": 50.0755, "lon": 14.4378, "cart_x": 3.0, "cart_y": 4.0},
    2: {"lat": 49.9000, "lon": 14.2000, "cart_x": 2.0, "cart_y": 4.0},
    3: {"lat": 49.0000, "lon": 14.5000, "cart_x": 2.0, "cart_y": 3.0},
    4: {"lat": 49.7400, "lon": 13.3700, "cart_x": 1.0, "cart_y": 3.0},
    5: {"lat": 50.2300, "lon": 12.8700, "cart_x": 0.0, "cart_y": 3.0},
    6: {"lat": 50.6100, "lon": 13.8300, "cart_x": 0.0, "cart_y": 4.0},
    7: {"lat": 50.7700, "lon": 15.0500, "cart_x": 1.0, "cart_y": 5.0},
    8: {"lat": 50.2100, "lon": 15.8300, "cart_x": 2.0, "cart_y": 5.0},
    9: {"lat": 49.9500, "lon": 16.3100, "cart_x": 3.0, "cart_y": 5.0},
    10: {"lat": 49.4000, "lon": 15.5000, "cart_x": 3.0, "cart_y": 3.0},
    11: {"lat": 49.2000, "lon": 16.6000, "cart_x": 4.0, "cart_y": 3.0},
    12: {"lat": 49.7000, "lon": 17.3000, "cart_x": 4.0, "cart_y": 4.0},
    13: {"lat": 49.2000, "lon": 17.7000, "cart_x": 5.0, "cart_y": 3.0},
    14: {"lat": 49.8000, "lon": 18.3000, "cart_x": 5.0, "cart_y": 4.0},
}

PARTY_CATALOG_2025: Sequence[Tuple[int, str, Tuple[str, ...]]] = (
    (1, "Rebelové", ("Rebelove",)),
    (
        2,
        "Moravské zemské hnutí",
        ("Moravske zemske hnuti", "Moravane", "MZH"),
    ),
    (
        3,
        "Jasný signál nezávislých",
        ("Jasny signal nezavislych", "JaSaN"),
    ),
    (
        4,
        "Výzva 2025",
        ("Vyzva 2025", "Výzva", "VYZVA 2025", "VYZVA", "VÝZVA 2025"),
    ),
    (
        5,
        "SMS - Stát má sloužit",
        (
            "SMS - Stat ma slouzit",
            "SMS",
            "SMSka",
            "SMS – Stát Má Sloužit",
        ),
    ),
    (
        6,
        "Svoboda a přímá demokracie (SPD)",
        (
            "SPD",
            "Svoboda a prima demokracie (SPD)",
            "Svoboda a pr. demokracie (SPD)",
        ),
    ),
    (
        7,
        "ČSSD - Česká suverenita sociální demokracie",
        (
            "CSSD - Ceska suverenita socialni demokracie",
            "Ceska str.socialne demokrat.",
            "ČSSD",
            "CSSD",
        ),
    ),
    (
        8,
        "Přísaha občanské hnutí",
        (
            "Prisaha obcanske hnuti",
            "PRISAHA Roberta Slachty",
            "Prisaha",
            "PŘÍSAHA",
        ),
    ),
    (9, "Levice", ("LEVICE",)),
    (
        10,
        "Česká republika na 1. místě",
        (
            "Ceska republika na 1. miste",
            "Ceska republika na 1. miste!",
            "ČR1",
            "CR1",
        ),
    ),
    (
        11,
        "Spolu (ODS, KDU-ČSL, TOP 09)",
        (
            "Spolu",
            "SPOLU",
            "SPOLU - ODS, KDU-CSL, TOP 09",
            "SPOLU (ODS, KDU-CSL, TOP 09)",
        ),
    ),
    (
        12,
        "Švýcarská demokracie",
        (
            "Svycarska demokracie",
            "Švýcarská dem.",
            "ŠVÝCARSKÁ DEMOKRACIE",
        ),
    ),
    (
        13,
        "Nevolte Urza.cz.",
        (
            "Nevolte Urza.cz",
            "Urza.cz: Nechceme vase hlasy",
            "Nevolte Urza",
            "Nevolte Urza cz",
        ),
    ),
    (
        14,
        "Hnutí občanů a podnikatelů",
        (
            "Hnuti obcanu a podnikatelu",
            "HOP",
            "HOP Hydra",
            "Hnutí občanů a podnikatelů (HOP Hydra)",
        ),
    ),
    (15, "Hnutí Generace", ("Hnuti Generace", "Generace")),
    (
        16,
        "Česká pirátská strana",
        (
            "Ceska piratska strana",
            "Piratska strana",
            "Pirati",
            "PIRATI",
            "PIRATI a STAROSTOVE",
        ),
    ),
    (
        17,
        "Koruna česká (monarchistická strana Čech Moravy a Slezska)",
        (
            "Koruna Ceska (monarch.strana)",
            "Koruna ceska",
            "Koruna Ceska (monarch.strana Cech, Moravy a Slezska)",
        ),
    ),
    (18, "Volt Česko", ("Volt Cesko", "Volt")),
    (
        19,
        "Volte Pravý Blok",
        (
            "Volte Pravy Blok",
            "Volte Pravý Blok www.cibulka.net",
            "Pravy Blok",
        ),
    ),
    (
        20,
        "Motoristé sobě",
        ("Motoriste sobe", "Motoriste", "AUTO", "Motoriste sobe!"),
    ),
    (
        21,
        "Balbínova poetická strana",
        ("Balbinova poeticka strana", "Balbinova", "BPS"),
    ),
    (22, "ANO 2011", ("ANO",)),
    (
        23,
        "Starostové a nezávislí",
        (
            "Starostove a nezavisli",
            "STAN",
            "STAROSTOVE",
            "Starostove",
            "PIRATI a STAROSTOVE",
        ),
    ),
    (24, "Hnutí Kruh", ("Hnuti Kruh", "Kruh")),
    (25, "Stačilo!", ("Stacilo!", "STACILO!", "Stacilo")),
    (
        26,
        "Voluntia",
        (
            "Voluntia, protoze dobrovol. je zakl. kamenem spol.",
            "Voluntia, protože dobrovol. je zákl. kamenem spol.",
            "Voluntia.cz",
        ),
    ),
)

PARTY_POSITION: Dict[str, int] = {
    # 2025 official roster
    "Rebelové": 7,
    "Moravské zemské hnutí": 4,
    "Jasný signál nezávislých": 5,
    "Výzva 2025": 6,
    "SMS - Stát má sloužit": 5,
    "Svoboda a přímá demokracie (SPD)": 8,
    "ČSSD - Česká suverenita sociální demokracie": 3,
    "Přísaha občanské hnutí": 6,
    "Levice": 1,
    "Česká republika na 1. místě": 7,
    "Spolu (ODS, KDU-ČSL, TOP 09)": 4,
    "Švýcarská demokracie": 3,
    "Nevolte Urza.cz.": 5,
    "Hnutí občanů a podnikatelů": 6,
    "Hnutí Generace": 5,
    "Česká pirátská strana": 2,
    "Koruna česká (monarchistická strana Čech Moravy a Slezska)": 5,
    "Volt Česko": 3,
    "Volte Pravý Blok": 7,
    "Motoristé sobě": 6,
    "Balbínova poetická strana": 5,
    "ANO 2011": 6,
    "Starostové a nezávislí": 3,
    "Hnutí Kruh": 5,
    "Stačilo!": 7,
    "Voluntia": 5,
    # Legacy labels retained for historical datasets
    "SPOLU - ODS, KDU-CSL, TOP 09": 4,
    "PIRATI a STAROSTOVE": 3,
    "Svoboda a pr. demokracie (SPD)": 8,
    "PRISAHA Roberta Slachty": 6,
    "Ceska str.socialne demokrat.": 3,
    "Komunisticka str.Cech a Moravy": 2,
    "Trikolora Svobodni Soukromnici": 7,
    "VOLNY blok": 8,
    "Strana zelenych": 2,
    "Otevreme CR normalnimu zivotu": 6,
    "Aliance pro budoucnost": 6,
    "Koruna Ceska (monarch.strana)": 5,
    "SENIORI 21": 4,
    "Urza.cz: Nechceme vase hlasy": 5,
}

HISTORICAL_COMPATIBILITY: Dict[frozenset, str] = {
    # 2025-aligned expectations
    frozenset({"Spolu (ODS, KDU-ČSL, TOP 09)", "Česká pirátská strana"}): "high",
    frozenset({"Spolu (ODS, KDU-ČSL, TOP 09)", "Starostové a nezávislí"}): "high",
    frozenset({"Spolu (ODS, KDU-ČSL, TOP 09)", "ANO 2011"}): "low",
    frozenset({"Česká pirátská strana", "Starostové a nezávislí"}): "high",
    frozenset({"ANO 2011", "Svoboda a přímá demokracie (SPD)"}): "medium",
    frozenset({"ANO 2011", "Motoristé sobě"}): "medium",
    frozenset({"ANO 2011", "Stačilo!"}): "low",
    frozenset({"Motoristé sobě", "Stačilo!"}): "medium",
    # Legacy relationships for archived datasets
    frozenset({"SPOLU - ODS, KDU-CSL, TOP 09", "PIRATI a STAROSTOVE"}): "high",
    frozenset({"SPOLU - ODS, KDU-CSL, TOP 09", "ANO 2011"}): "low",
    frozenset({"ANO 2011", "Svoboda a pr. demokracie (SPD)"}): "medium",
    frozenset({"ANO 2011", "PIRATI a STAROSTOVE"}): "low",
}

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "title": "Czech Parliamentary Elections Results Dashboard",
        "subtitle": "National overview for {year}",
        "data_options": "Data options",
        "primary_year": "Primary election year",
        "fallback_option": "Show {fallback_year} data if {primary_year} results are unavailable",
        "fallback_notice": "{requested_year} results are not live yet. Displaying archived data for {effective_year}.",
        "results_loaded": "Results for {year} loaded successfully.",
        "headline_processed": "Processed precincts",
        "headline_turnout": "National turnout",
        "headline_leading": "Leading party",
        "headline_source": "Live feed",
        "majority_tracker": "Majority tracker",
        "seats_to_majority": "Seats to 101",
        "seats_to_majority_help": "Seats the leading party needs to reach a simple majority (101/200).",
        "leading_party_seats": "Leading party seats",
        "seats_reported": "Seats reported",
        "hemicycle_projection": "Hemicycle projection",
        "coalition_toggle": "Colour seats by",
        "colour_by_party": "Party",
        "colour_by_coalition": "Coalition grouping",
        "hemicycle_caption": "Hover to inspect party seat blocks; coalition colouring reflects preset alliances.",
        "coalition_builder": "Coalition builder",
        "coalition_instruction": "Select parties to explore potential majorities:",
        "combined_seats": "Combined seats",
        "majority_question": "≥ 101?",
        "coalition_type": "Coalition type",
        "coalition_caption": "{parties} → {seats} seats ({threshold} needed for a majority).",
        "coalition_type_minimal": "minimal-winning",
        "coalition_type_oversized": "oversized",
        "coalition_type_below": "below",
        "coalition_type_prompt": "Select parties to see coalition status.",
        "threshold_watchlist": "Threshold watchlist",
        "threshold_waiting": "Party-level results are not available yet.",
        "threshold_status_safe": "safe",
        "threshold_status_knife": "knife-edge",
        "threshold_status_below": "below",
        "vote_share_section": "National vote share & swing",
        "vote_share_waiting": "Party-level results are not available yet.",
        "map_leading": "Who's leading where",
        "map_missing": "Regional breakdowns are not available yet.",
        "map_caption": "Source: volby.cz region feed. Cartogram mode equalises area so Prague stays legible.",
        "map_view_label": "View mode",
        "map_view_geographic": "Geographic",
        "map_view_cartogram": "Cartogram",
        "heatmap_title": "Counting progress heatmap",
        "heatmap_waiting": "Waiting for regional progress data.",
        "heatmap_caption": "Turnout choropleth will replace this view once precinct-level turnout feeds are exposed.",
        "abroad_vote_lens": "Abroad vote lens",
        "abroad_placeholder": "Abroad and embassy vote batches are published later on election night. This panel will light up once the opendata endpoint is ingested.",
        "seats_by_region": "Seats by region",
        "seats_by_region_placeholder": "Regional seat allocation feed is pending; this panel will activate once available.",
        "preference_leaderboard": "Preference votes leaderboard",
        "preference_placeholder": "Preferential vote totals (kroužky) are published later in the night. Parsing support is on the roadmap.",
        "wasted_vote_meter": "Wasted vote meter",
        "wasted_votes_metric": "Wasted votes",
        "share_of_valid": "Share of valid",
        "wasted_waiting": "Vote totals are not available yet.",
        "wasted_caption": "Votes cast for subjects under the legal threshold do not translate into seats and are redistributed proportionally.",
        "paths_to_majority": "Paths to 101",
        "paths_waiting": "Seat projection not available yet.",
        "paths_none": "No coalition combinations reach the 101-seat threshold yet.",
        "paths_caption": "Combinations sorted by smallest majority first, then ideological spread (lower is closer). Historical compatibility is a lightweight heuristic.",
        "download_data": "Download data",
        "download_parties": "Download party results (CSV)",
        "download_regions": "Download regional leaders (CSV)",
        "wasted_parties": "Below-threshold parties",
        "yes": "Yes",
        "no": "No",
        "source_footer": "Data source: Czech Statistical Office – volby.cz (scraped live when the dashboard loads).",
        "provenance_scope_headline": "National headline feed",
        "provenance_scope_majority": "Seat allocation feed",
        "provenance_scope_votes": "Party vote feed",
        "provenance_scope_regions": "Regional map feed",
        "provenance_scope_deepdive": "Analytical overlays",
        "uncertainty_header": "Uncertainty & outstanding precincts",
        "uncertainty_partial": "{processed:.1f}% of precincts reported; seat projection may swing by approximately ±{swing} seats.",
        "uncertainty_full": "All precincts processed. Projections now match the certified results.",
        "share_tools_header": "Pin & share",
        "share_tools_caption": "Copy this link to reopen the dashboard with the current language and coalition selection.",
        "share_tools_button": "Update link",
        "cache_hit_label": "cached copy",
        "cache_revalidated_label": "revalidated",
    },
    "cs": {
        "title": "Dashboard výsledků voleb do Poslanecké sněmovny",
        "subtitle": "Celostátní přehled pro rok {year}",
        "data_options": "Nastavení dat",
        "primary_year": "Primární rok voleb",
        "fallback_option": "Zobrazit data {fallback_year}, pokud výsledky {primary_year} ještě nejsou dostupné",
        "fallback_notice": "Výsledky {requested_year} nejsou zatím živě. Zobrazují se archivní data {effective_year}.",
        "results_loaded": "Výsledky pro rok {year} načteny.",
        "headline_processed": "Sečtené okrsky",
        "headline_turnout": "Celostátní účast",
        "headline_leading": "Vedoucí subjekt",
        "headline_source": "Živý přenos",
        "majority_tracker": "Sledování většiny",
        "seats_to_majority": "Mandáty do 101",
        "seats_to_majority_help": "Kolik mandátů chybí vedoucímu subjektu do prosté většiny (101/200).",
        "leading_party_seats": "Mandáty lídra",
        "seats_reported": "Sečtené mandáty",
        "hemicycle_projection": "Projektovaná sněmovna",
        "coalition_toggle": "Barvy křesel",
        "colour_by_party": "Subjekt",
        "colour_by_coalition": "Koalice",
        "hemicycle_caption": "Najetím odhalíte bloky mandátů, koaliční režim vybarví přednastavené aliance.",
        "coalition_builder": "Stavitel koalic",
        "coalition_instruction": "Vyberte subjekty a zjistěte, zda dávají většinu:",
        "combined_seats": "Součet mandátů",
        "majority_question": "≥ 101?",
        "coalition_type": "Typ koalice",
        "coalition_caption": "{parties} → {seats} mandátů (na většinu je třeba {threshold}).",
        "coalition_type_minimal": "minimální vítězná",
        "coalition_type_oversized": "předimenzovaná",
        "coalition_type_below": "pod většinou",
        "coalition_type_prompt": "Vyberte strany a zobrazí se stav koalice.",
        "threshold_watchlist": "Hlídání klauzule",
        "threshold_waiting": "Údaje o stranách zatím nejsou k dispozici.",
        "threshold_status_safe": "bezpečně",
        "threshold_status_knife": "na hraně",
        "threshold_status_below": "pod klauzulí",
        "vote_share_section": "Národní podíl hlasů a swing",
        "vote_share_waiting": "Údaje o stranách zatím nejsou k dispozici.",
        "map_leading": "Kde kdo vede",
        "map_missing": "Krajská rozpadnutí zatím nejsou k dispozici.",
        "map_caption": "Zdroj: feed volby.cz. Kartogram srovná plochy, aby Praha nepřebila mapu.",
        "map_view_label": "Zobrazení",
        "map_view_geographic": "Mapa",
        "map_view_cartogram": "Kartogram",
        "heatmap_title": "Mapa postupu sčítání",
        "heatmap_waiting": "Čekáme na krajská data o postupu sčítání.",
        "heatmap_caption": "Jakmile dorazí účast za okrsky, nahradí tuto mapu choropleth účasti.",
        "abroad_vote_lens": "Hlasování v zahraničí",
        "abroad_placeholder": "Hlasy z ambasád dorážejí se zpožděním. Panel se rozsvítí po napojení opendat.",
        "seats_by_region": "Mandáty podle krajů",
        "seats_by_region_placeholder": "Krajská distribuce mandátů chybí; panel se aktivuje po načtení feedu.",
        "preference_leaderboard": "Žebříček preferenčních hlasů",
        "preference_placeholder": "Preferenční hlasy (kroužky) zveřejní ČSÚ později – zpracování je v plánu.",
        "wasted_vote_meter": "Vyhozené hlasy",
        "wasted_votes_metric": "Vyhozené hlasy",
        "share_of_valid": "Podíl na platných",
        "wasted_waiting": "Údaje o hlasech zatím nejsou k dispozici.",
        "wasted_caption": "Hlasy pro subjekty pod klauzulí se nepřetaví v mandáty a přerozdělují se ostatním.",
        "paths_to_majority": "Cesty k 101",
        "paths_waiting": "Projekce mandátů zatím chybí.",
        "paths_none": "Žádná kombinace zatím nedosáhne na 101 mandátů.",
        "paths_caption": "Seřazeno od nejtěsnější většiny, pak podle ideologické vzdálenosti. Historická kompatibilita je orientační.",
        "download_data": "Stáhnout data",
        "download_parties": "Stáhnout výsledky subjektů (CSV)",
        "download_regions": "Stáhnout lídry krajů (CSV)",
        "wasted_parties": "Subjekty pod klauzulí",
        "yes": "Ano",
        "no": "Ne",
        "source_footer": "Zdroj dat: Český statistický úřad – volby.cz (získáváno při načtení dashboardu).",
        "provenance_scope_headline": "Národní souhrnný feed",
        "provenance_scope_majority": "Feed s mandáty",
        "provenance_scope_votes": "Feed s hlasy subjektů",
        "provenance_scope_regions": "Krajský mapový feed",
        "provenance_scope_deepdive": "Analytické nadstavby",
        "uncertainty_header": "Nejistota a zbývající okrsky",
        "uncertainty_partial": "Sečteno {processed:.1f}% okrsků; projekce mandátů se může měnit asi o ±{swing} mandátů.",
        "uncertainty_full": "Všechny okrsky jsou sečteny. Projekce odpovídají finálním výsledkům.",
        "share_tools_header": "Připnout a sdílet",
        "share_tools_caption": "Zkopírujte tento odkaz a otevřete dashboard ve stejném jazyce a s aktuálním výběrem koalic.",
        "share_tools_button": "Aktualizovat odkaz",
        "cache_hit_label": "uložená kopie",
        "cache_revalidated_label": "znovu ověřeno",
    },
}


def get_translator(language: str):
    lang = language if language in STRINGS else "en"

    def translate(key: str, fallback: str = "", **kwargs: Any) -> str:
        template = STRINGS.get(lang, {}).get(key)
        if template is None:
            template = STRINGS["en"].get(key, fallback or key)
        return template.format(**kwargs)

    return translate


COALITION_PRESETS: Sequence[Tuple[str, Sequence[str]]] = (
    (
        "SPOLU",
        (
            "SPOLU",
            "SPOLU - ODS, KDU-CSL, TOP 09",
        ),
    ),
    (
        "SPOLU + STAN + Pirates",
        (
            "SPOLU",
            "SPOLU - ODS, KDU-CSL, TOP 09",
            "STAROSTOVE",
            "STAROSTOVE A NEZAVISLI",
            "STAN",
            "PIRATI",
            "CESKA PIRATSKA STRANA",
            "Ceska piratska strana",
        ),
    ),
    (
        "ANO + SPD",
        (
            "ANO",
            "ANO 2011",
            "SPD",
            "Svoboda a pr. demokracie (SPD)",
        ),
    ),
    (
        "ANO + SPD + Motorists",
        (
            "ANO",
            "ANO 2011",
            "SPD",
            "Svoboda a pr. demokracie (SPD)",
            "Motoriste",
            "Motoriste sobe",
            "Motoristé",
            "Motoristé sobě",
            "AUTO",
        ),
    ),
    (
        "ANO + Motorists",
        (
            "ANO",
            "ANO 2011",
            "Motoriste",
            "Motoriste sobe",
            "Motoristé",
            "Motoristé sobě",
            "AUTO",
        ),
    ),
    (
        "ANO + Stačilo!",
        (
            "ANO",
            "ANO 2011",
            "STACILO!",
            "Stačilo!",
        ),
    ),
    (
        "Pirates + Mayors",
        (
            "PIRATI",
            "PIRATI a STAROSTOVE",
            "STAROSTOVE",
            "STAROSTOVE A NEZAVISLI",
        ),
    ),
    (
        "Democratic Bloc",
        (
            "SPOLU",
            "SPOLU - ODS, KDU-CSL, TOP 09",
            "PIRATI",
            "PIRATI a STAROSTOVE",
        ),
    ),
)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_election_dataset(year: int, fallback: Optional[int]) -> Dict[str, Any]:
    """Fetch election data with simple caching to avoid repeat scraping."""

    return gather_election_data(year=year, fallback_year=fallback)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_reference_dataset(year: int) -> Optional[Dict[str, Any]]:
    """Fetch a reference year dataset used for swing comparisons."""

    try:
        return gather_election_data(year=year, fallback_year=None)
    except ElectionDataUnavailable:
        return None


def fmt_number(value: Optional[int]) -> str:
    if value is None:
        return "–"
    return f"{value:,}".replace(",", "\u202f")


def fmt_percent(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "–"
    return f"{value:.{decimals}f}%"


def fmt_signed_percent(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "–"
    return f"{value:+.{decimals}f}%"


def normalize_key(label: str) -> str:
    """Normalize party labels for lookups and comparisons."""

    ascii_form = (
        unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]", "", ascii_form.lower())


CANONICAL_PARTY_LOOKUP_2025: Dict[str, str] = {}
PARTY_ALIASES_LOOKUP_2025: Dict[str, Tuple[str, ...]] = {}
OFFICIAL_PARTY_ORDER_2025: Dict[str, int] = {}
for draw_number, official_name, aliases in PARTY_CATALOG_2025:
    alias_family = (official_name, *aliases)
    PARTY_ALIASES_LOOKUP_2025[official_name] = alias_family
    OFFICIAL_PARTY_ORDER_2025[official_name] = draw_number
    for alias in alias_family:
        CANONICAL_PARTY_LOOKUP_2025[normalize_key(alias)] = official_name


def canonical_party_name(label: str) -> str:
    """Return the official 2025 label when available."""

    return CANONICAL_PARTY_LOOKUP_2025.get(normalize_key(label), label)


def alias_bundle(*canonical_names: str) -> Tuple[str, ...]:
    """Return a combined alias tuple for the requested canonical parties."""

    seen: Dict[str, str] = {}
    bundle: List[str] = []
    for name in canonical_names:
        aliases = PARTY_ALIASES_LOOKUP_2025.get(name)
        if not aliases:
            aliases = (name,)
        for alias in aliases:
            key = normalize_key(alias)
            if key not in seen:
                seen[key] = alias
                bundle.append(alias)
    return tuple(bundle)


def infer_coalition_size(name: str) -> int:
    """Guess coalition size based on separators in the subject name."""

    tokens = re.split(r"\s*(?:\+|/|,| a | & )\s*", name, flags=re.IGNORECASE)
    tokens = [token for token in tokens if token]
    return max(1, len(tokens))


def threshold_for_subject(name: str) -> float:
    size = infer_coalition_size(name)
    if size == 1:
        return 5.0
    if size == 2:
        return 8.0
    return 11.0


def coalition_status(vote_share: float, threshold: float) -> str:
    margin = vote_share - threshold
    if margin >= 1.0:
        return "safe"
    if margin >= -0.5:
        return "knife-edge"
    return "below"


def resolve_preset_parties(
    available: Sequence[str], preset: Sequence[str]
) -> List[str]:
    """Resolve preset aliases to actual party names from the dataset."""

    available_map = {normalize_key(name): name for name in available}
    resolved: List[str] = []
    for alias in preset:
        alias_key = normalize_key(alias)
        match = available_map.get(alias_key)
        if not match:
            for key, name in available_map.items():
                if alias_key and alias_key in key:
                    match = name
                    break
        if match and match not in resolved:
            resolved.append(match)
    return resolved


def build_party_color_map(parties_df: pd.DataFrame) -> Dict[str, str]:
    color_map: Dict[str, str] = {}
    palette = DEFAULT_PARTY_COLORS
    for index, row in enumerate(parties_df.itertuples(index=False), start=0):
        color = getattr(row, "color", None)
        party = getattr(row, "party")
        if isinstance(color, str) and color.startswith("#"):
            color_map[party] = color
        else:
            color_map[party] = palette[index % len(palette)]
    color_map.setdefault("Unfilled", "#d0d0d0")
    return color_map


def hex_to_rgba(hex_color: Optional[str], alpha: int = 200) -> List[int]:
    if not hex_color:
        return [120, 120, 120, alpha]
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except ValueError:
        return [120, 120, 120, alpha]
    return [r, g, b, alpha]


def build_provenance_badge(metadata: Dict[str, Any], scope_key: str, t) -> str:
    fetched_at = metadata.get("fetched_at")
    if fetched_at:
        timestamp = datetime.fromtimestamp(fetched_at).strftime("%H:%M:%S")
    else:
        timestamp = "—"
    source = metadata.get("source", "volby.cz")
    scope_label = t(scope_key)
    return f"📡 {scope_label} · {source} · {timestamp}"


def make_parties_dataframe(
    parties: Sequence[Any],
    seats: Sequence[Any],
    *,
    effective_year: Optional[int] = None,
) -> pd.DataFrame:
    if not parties:
        return pd.DataFrame()
    parties_df = pd.DataFrame([vars(party) for party in parties])
    parties_df.rename(
        columns={
            "number": "party_number",
            "name": "party",
            "votes": "votes",
            "vote_share": "vote_share",
        },
        inplace=True,
    )
    canonicalize = effective_year is not None and effective_year >= 2025

    parties_df["votes"] = (
        pd.to_numeric(parties_df["votes"], errors="coerce").fillna(0).astype(int)
    )
    parties_df["vote_share"] = pd.to_numeric(
        parties_df["vote_share"], errors="coerce"
    ).fillna(0.0)
    if canonicalize:
        parties_df["party"] = parties_df["party"].apply(canonical_party_name)
        parties_df["official_draw"] = parties_df["party"].map(OFFICIAL_PARTY_ORDER_2025)
        if "party_number" in parties_df.columns:
            parties_df["party_number"] = parties_df["official_draw"].fillna(
                parties_df["party_number"]
            )
    parties_df["party_key"] = parties_df["party"].apply(normalize_key)
    parties_df["threshold"] = parties_df["party"].apply(threshold_for_subject)
    parties_df["status"] = parties_df.apply(
        lambda row: coalition_status(row["vote_share"], row["threshold"]), axis=1
    )

    def accumulate_seat_maps(
        canonical: bool,
    ) -> Tuple[Dict[str, int], Dict[str, Optional[str]]]:
        seat_totals: Dict[str, int] = {}
        seat_colors: Dict[str, Optional[str]] = {}
        for seat in seats:
            name = canonical_party_name(seat.party) if canonical else seat.party
            mandates = getattr(seat, "mandates", 0)
            seat_totals[name] = seat_totals.get(name, 0) + mandates
            color = getattr(seat, "color", None)
            if name not in seat_colors or color:
                seat_colors[name] = color
        return seat_totals, seat_colors

    seat_map, color_map = accumulate_seat_maps(canonicalize)
    parties_df["mandates"] = parties_df["party"].map(seat_map).fillna(0).astype(int)
    parties_df["color"] = parties_df["party"].map(color_map)
    parties_df.sort_values(
        ["vote_share", "votes"], ascending=[False, False], inplace=True
    )
    parties_df.reset_index(drop=True, inplace=True)
    return parties_df


def make_seats_dataframe(
    seats: Sequence[Any], *, effective_year: Optional[int] = None
) -> pd.DataFrame:
    if not seats:
        return pd.DataFrame()
    seats_df = pd.DataFrame([vars(seat) for seat in seats])
    seats_df.rename(columns={"party": "party", "mandates": "mandates"}, inplace=True)
    canonicalize = effective_year is not None and effective_year >= 2025
    seats_df["mandates"] = (
        pd.to_numeric(seats_df["mandates"], errors="coerce").fillna(0).astype(int)
    )
    if canonicalize:
        seats_df["party"] = seats_df["party"].apply(canonical_party_name)
        agg_map: Dict[str, Any] = {"mandates": "sum"}
        if "color" in seats_df.columns:
            agg_map["color"] = "first"
        seats_df = seats_df.groupby("party", as_index=False).agg(agg_map)
        if "color" not in seats_df.columns:
            seats_df["color"] = None
        seats_df["mandates"] = seats_df["mandates"].astype(int)
    seats_df["party_key"] = seats_df["party"].apply(normalize_key)
    seats_df.sort_values("mandates", ascending=False, inplace=True)
    seats_df.reset_index(drop=True, inplace=True)
    return seats_df


def apply_baseline_swing(
    parties_df: pd.DataFrame, baseline_df: Optional[pd.DataFrame]
) -> pd.DataFrame:
    if parties_df.empty:
        return parties_df
    if baseline_df is None or baseline_df.empty:
        parties_df["swing"] = float("nan")
        return parties_df
    baseline_map = baseline_df.set_index("party_key")["vote_share"].to_dict()
    parties_df["swing"] = parties_df.apply(
        lambda row: row["vote_share"] - baseline_map.get(row["party_key"], 0.0), axis=1
    )
    return parties_df


def compute_leading_party(parties_df: pd.DataFrame) -> Optional[pd.Series]:
    if parties_df.empty:
        return None
    return parties_df.iloc[0]


def compute_leading_region_counts(regions_df: pd.DataFrame) -> Dict[str, int]:
    if regions_df.empty:
        return {}
    counts: Dict[str, int] = defaultdict(int)
    for _, row in regions_df.iterrows():
        counts[row["leading_party"]] += 1
    return counts


def generate_hemicycle_layout(
    total_seats: int, rows: int = 10
) -> List[Tuple[float, float]]:
    """Return x,y coordinates for a semi-circular seating layout."""

    layout: List[Tuple[float, float]] = []
    remaining = total_seats
    row_counts: List[int] = []
    for row in range(rows):
        rows_left = rows - row
        seats_in_row = max(1, math.ceil(remaining / rows_left))
        row_counts.append(seats_in_row)
        remaining -= seats_in_row
    total_allocated = sum(row_counts)
    if total_allocated > total_seats:
        row_counts[-1] -= total_allocated - total_seats
    elif total_allocated < total_seats:
        row_counts[-1] += total_seats - total_allocated

    for radius_index, seats_in_row in enumerate(row_counts, start=1):
        if seats_in_row <= 0:
            continue
        angles = [math.pi * (i + 0.5) / seats_in_row for i in range(seats_in_row)]
        radius = rows - radius_index + 1
        scaling = radius / rows
        for angle in angles:
            x = scaling * math.cos(angle)
            y = scaling * math.sin(angle)
            layout.append((x, y))
    return layout[:total_seats]


def build_hemicycle_dataframe(
    seats_df: pd.DataFrame, color_map: Dict[str, str]
) -> pd.DataFrame:
    total_mandates = int(seats_df["mandates"].sum()) if not seats_df.empty else 0
    total = max(total_mandates, SEAT_TARGET)
    coordinates = generate_hemicycle_layout(total)
    records = []
    seat_index = 0
    for _, row in seats_df.iterrows():
        party = row["party"]
        mandates = int(row["mandates"])
        for _ in range(mandates):
            if seat_index >= len(coordinates):
                break
            x, y = coordinates[seat_index]
            records.append(
                {
                    "party": party,
                    "x": x,
                    "y": y,
                    "seat_index": seat_index + 1,
                    "color": color_map.get(party),
                }
            )
            seat_index += 1
    while seat_index < len(coordinates) and seat_index < SEAT_TARGET:
        x, y = coordinates[seat_index]
        records.append(
            {
                "party": "Unfilled",
                "x": x,
                "y": y,
                "seat_index": seat_index + 1,
                "color": "#d0d0d0",
            }
        )
        seat_index += 1
    return pd.DataFrame(records)


def render_headline_bar(
    metadata: Dict[str, Any], summary: Dict[str, Any], parties_df: pd.DataFrame, t
) -> None:
    leading = compute_leading_party(parties_df)
    processed_pct = summary.get("wards_processed_percent")
    turnout_pct = summary.get("turnout_percent")
    wards_processed = summary.get("wards_processed")
    wards_total = summary.get("wards_total")
    fetched_at = metadata.get("fetched_at")
    timestamp = datetime.fromtimestamp(fetched_at) if fetched_at else None

    with st.container():
        headline_cols = st.columns([2, 2, 2, 3])
        headline_cols[0].metric(
            t("headline_processed"),
            (
                f"{fmt_number(wards_processed)} / {fmt_number(wards_total)}"
                if wards_processed is not None and wards_total is not None
                else fmt_number(wards_processed)
            ),
            delta=fmt_percent(processed_pct) if processed_pct is not None else None,
        )
        headline_cols[1].metric(
            t("headline_turnout"),
            fmt_percent(turnout_pct) if turnout_pct is not None else "–",
        )

        if leading is not None:
            headline_cols[2].metric(
                t("headline_leading"),
                leading["party"],
                delta=fmt_percent(float(leading["vote_share"])),
            )
        else:
            headline_cols[2].write("–")

        source_label = "ČSÚ · volby.cz"
        if timestamp:
            source_label += f" · updated {timestamp:%H:%M:%S}"
        cache_meta = metadata.get("cache") if isinstance(metadata, dict) else None
        if isinstance(cache_meta, dict) and cache_meta.get("hit"):
            cache_tag = t("cache_hit_label")
            if cache_meta.get("revalidated"):
                cache_tag = t("cache_revalidated_label")
            source_label += f" · {cache_tag}"
        headline_cols[3].write(f"**{t('headline_source')}**: {source_label}")


def render_majority_tracker(seats_df: pd.DataFrame, t) -> None:
    st.markdown(f"### {t('majority_tracker')}")
    total_mandates = int(seats_df["mandates"].sum()) if not seats_df.empty else 0
    leading_seats = int(seats_df["mandates"].max()) if not seats_df.empty else 0
    leading_party = seats_df.iloc[0]["party"] if not seats_df.empty else "—"
    seats_to_majority = max(MAJORITY_THRESHOLD - leading_seats, 0)

    cols = st.columns([2, 1, 1])
    cols[0].metric(
        t("seats_to_majority"),
        seats_to_majority,
        help=t("seats_to_majority_help"),
    )
    cols[1].metric(t("leading_party_seats"), leading_seats, help=leading_party)
    cols[2].metric(
        t("seats_reported"), total_mandates, help=f"Total chamber seats: {SEAT_TARGET}"
    )


def render_hemicycle(
    seats_df: pd.DataFrame, parties_df: pd.DataFrame, regions_df: pd.DataFrame, t
) -> None:
    st.markdown(f"### {t('hemicycle_projection')}")
    if seats_df.empty:
        st.info("Seat allocation has not been announced yet.")
        return

    color_map = build_party_color_map(parties_df)
    hemicycle_df = build_hemicycle_dataframe(seats_df, color_map)

    mode_options = {
        t("colour_by_party", "Party"): "party",
        t("colour_by_coalition", "Coalition grouping"): "coalition",
    }
    group_mode_label = st.radio(
        t("coalition_toggle"),
        options=list(mode_options.keys()),
        horizontal=True,
    )
    group_mode = mode_options.get(group_mode_label, "party")

    if group_mode == "coalition":
        coalition_map = assign_coalition_groups(parties_df["party"].tolist())
        hemicycle_df["display_group"] = (
            hemicycle_df["party"]
            .astype(str)
            .apply(lambda name: coalition_map.get(name, name))
        )
    else:
        hemicycle_df["display_group"] = hemicycle_df["party"]

    base_chart = (
        alt.Chart(hemicycle_df)
        .mark_circle(size=180)
        .encode(
            x=alt.X("x", axis=None),
            y=alt.Y("y", axis=None),
            color=alt.Color("display_group", title=""),
            tooltip=["party", alt.Tooltip("seat_index", title="Seat #")],
        )
        .properties(width="container", height=320)
    )
    st.altair_chart(base_chart, use_container_width=True)

    st.caption(t("hemicycle_caption"))


def assign_coalition_groups(parties: Sequence[str]) -> Dict[str, str]:
    groups: Dict[str, str] = {}
    for preset_name, preset_parties in COALITION_PRESETS:
        resolved = resolve_preset_parties(parties, preset_parties)
        if not resolved:
            continue
        for party in resolved:
            groups[party] = preset_name
    return groups


def render_coalition_builder(parties_df: pd.DataFrame, t) -> None:
    st.markdown(f"### {t('coalition_builder')}")
    if parties_df.empty:
        st.info(t("threshold_waiting"))
        return

    seat_map = parties_df.set_index("party")["mandates"].to_dict()
    party_names = parties_df["party"].tolist()
    if "official_draw" in parties_df.columns:
        draw_order = parties_df.set_index("party")["official_draw"].to_dict()

        def sort_key(name: str) -> Tuple[float, str]:
            draw = draw_order.get(name)
            if pd.isna(draw):
                return (float("inf"), name)
            return (float(draw), name)

        party_names = sorted(party_names, key=sort_key)

    if "coalition_selection" not in st.session_state:
        st.session_state["coalition_selection"] = []

    preset_cols = st.columns(len(COALITION_PRESETS)) if COALITION_PRESETS else []
    for idx, (label, preset) in enumerate(COALITION_PRESETS):
        available = resolve_preset_parties(party_names, preset)
        if not available:
            continue
        if preset_cols and idx < len(preset_cols) and preset_cols[idx].button(label):
            st.session_state["coalition_selection"] = available
            for party in party_names:
                st.session_state[f"coalition_{party}"] = party in available

    st.write(t("coalition_instruction"))
    columns_count = min(4, max(1, len(party_names)))
    checkbox_cols = st.columns(columns_count)
    for index, party in enumerate(party_names):
        column = checkbox_cols[index % columns_count]
        default_checked = party in st.session_state.get("coalition_selection", [])
        checkbox_key = f"coalition_{party}"
        if checkbox_key not in st.session_state:
            checked = column.checkbox(party, value=default_checked, key=checkbox_key)
        else:
            checked = column.checkbox(party, key=checkbox_key)
        if checked and party not in st.session_state.setdefault(
            "coalition_selection", []
        ):
            st.session_state["coalition_selection"].append(party)
        elif not checked and party in st.session_state.setdefault(
            "coalition_selection", []
        ):
            st.session_state["coalition_selection"].remove(party)

    selected_parties = [
        party for party in party_names if st.session_state.get(f"coalition_{party}")
    ]
    total_seats = sum(seat_map.get(party, 0) for party in selected_parties)

    if total_seats >= MAJORITY_THRESHOLD and selected_parties:
        minimal_majority = all(
            (total_seats - seat_map.get(party, 0)) < MAJORITY_THRESHOLD
            for party in selected_parties
        )
        status_key = (
            "coalition_type_minimal" if minimal_majority else "coalition_type_oversized"
        )
    elif selected_parties:
        status_key = "coalition_type_below"
    else:
        status_key = "coalition_type_prompt"

    info_col1, info_col2, info_col3 = st.columns(3)
    info_col1.metric(t("combined_seats"), total_seats)
    info_col2.metric(
        t("majority_question"),
        t("yes") if total_seats >= MAJORITY_THRESHOLD else t("no"),
    )
    if status_key == "coalition_type_prompt":
        info_col3.write(t("coalition_type_prompt"))
    else:
        info_col3.write(f"{t('coalition_type')}: **{t(status_key)}**")

    if selected_parties:
        st.caption(
            t(
                "coalition_caption",
                parties=" + ".join(selected_parties),
                seats=total_seats,
                threshold=MAJORITY_THRESHOLD,
            )
        )


def render_threshold_watchlist(parties_df: pd.DataFrame, t) -> None:
    st.markdown(f"### {t('threshold_watchlist')}")
    if parties_df.empty:
        st.info(t("threshold_waiting"))
        return

    watch_df = parties_df[parties_df["vote_share"] > 0].copy()
    watch_df["distance"] = watch_df["vote_share"] - watch_df["threshold"]
    focus = watch_df[
        (watch_df["vote_share"] >= watch_df["threshold"] - 3)
        & (watch_df["vote_share"] <= watch_df["threshold"] + 3)
    ]
    if not focus.empty:
        watch_df = focus
    watch_df = watch_df.sort_values("distance")
    watch_df = watch_df.head(8)

    if watch_df.empty:
        st.info(t("threshold_waiting"))
        return

    cards = st.columns(min(4, len(watch_df)))
    for index, (_, row) in enumerate(watch_df.iterrows()):
        column = cards[index % len(cards)]
        with column.container():
            column.markdown(f"**{row['party']}**")
            column.metric(
                "Current share",
                fmt_percent(float(row["vote_share"])),
                delta=fmt_signed_percent(float(row["distance"])),
            )
            status_key = f"threshold_status_{row['status']}"
            status_label = t(status_key, row["status"])
            column.caption(
                f"Threshold {row['threshold']:.1f}% · Status: {status_label}"
            )


def render_vote_share_section(parties_df: pd.DataFrame, t) -> None:
    st.markdown(f"### {t('vote_share_section')}")
    if parties_df.empty:
        st.info(t("vote_share_waiting"))
        return

    top_df = parties_df.head(12)
    chart = (
        alt.Chart(top_df)
        .mark_bar()
        .encode(
            x=alt.X("vote_share", title="Vote share (%)"),
            y=alt.Y("party", sort="-x"),
            color=alt.Color(
                "swing",
                legend=alt.Legend(title="Swing vs 2021"),
                scale=alt.Scale(scheme="redblue", domainMid=0),
            ),
            tooltip=[
                alt.Tooltip("party"),
                alt.Tooltip("vote_share", title="Vote share", format=".2f"),
                alt.Tooltip("mandates", title="Seats"),
                alt.Tooltip("swing", title="Swing vs 2021", format="+.2f"),
            ],
        )
        .properties(height=360)
    )
    st.altair_chart(chart, use_container_width=True)

    st.dataframe(
        parties_df[
            ["party", "votes", "vote_share", "mandates", "threshold", "status", "swing"]
        ],
        hide_index=True,
        use_container_width=True,
    )


def render_region_map(
    parties_df: pd.DataFrame, regions_df: pd.DataFrame, t
) -> pd.DataFrame:
    st.markdown(f"### {t('map_leading')}")
    if regions_df.empty:
        st.info(t("map_missing"))
        return pd.DataFrame()

    base = regions_df.copy()
    for column in ("leading_percent", "processed_percent", "votes"):
        if column in base.columns:
            base[column] = pd.to_numeric(base[column], errors="coerce")

    coords = pd.DataFrame.from_dict(REGION_COORDINATES, orient="index")
    coords.index.name = "region_id"
    coords.reset_index(inplace=True)
    base = base.merge(coords, on="region_id", how="left")
    base.dropna(subset=["lat", "lon"], inplace=True)

    if base.empty:
        st.info("Region coordinate metadata is missing.")
        return base

    color_map = build_party_color_map(parties_df)
    base["color_rgba"] = (
        base["leading_party"]
        .fillna("")
        .astype(str)
        .map(lambda party: hex_to_rgba(color_map.get(party) if party else None))
    )
    base["radius"] = (
        base["processed_percent"].fillna(0).apply(lambda pct: 25000 + pct * 400)
    )

    mode_options = {
        t("map_view_geographic", "Geographic"): "geographic",
        t("map_view_cartogram", "Cartogram"): "cartogram",
    }
    view_label = st.radio(
        t("map_view_label"), list(mode_options.keys()), horizontal=True
    )
    view_mode = mode_options.get(view_label, "geographic")
    tooltip_text = "Region: {region}\nLeading: {leading_party} ({leading_percent:.1f}%)\nProcessed: {processed_percent:.1f}%"

    if view_mode == "geographic":
        deck = pdk.Deck(
            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
            initial_view_state=pdk.ViewState(
                latitude=49.8, longitude=15.5, zoom=6.2, pitch=0
            ),
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    data=base,
                    get_position="[lon, lat]",
                    get_radius="radius",
                    get_fill_color="color_rgba",
                    pickable=True,
                    stroked=False,
                )
            ],
            tooltip=cast(Any, {"text": tooltip_text}),
        )
        st.pydeck_chart(deck)
    else:
        cart_chart = (
            alt.Chart(base)
            .mark_circle()
            .encode(
                x=alt.X("cart_x", axis=None),
                y=alt.Y("cart_y", axis=None),
                color=alt.Color("leading_party", title="Leading party"),
                size=alt.Size(
                    "processed_percent",
                    title="Processed %",
                    scale=alt.Scale(range=[200, 2000]),
                ),
                tooltip=[
                    alt.Tooltip("region", title="Region"),
                    alt.Tooltip("leading_party", title="Leading party"),
                    alt.Tooltip("leading_percent", title="Vote share", format=".1f"),
                    alt.Tooltip("processed_percent", title="Processed", format=".1f"),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(cart_chart, use_container_width=True)

    st.caption(t("map_caption"))
    return base


def render_turnout_cartogram(region_map: pd.DataFrame, t) -> None:
    st.markdown(f"### {t('heatmap_title')}")
    if region_map.empty:
        st.info(t("heatmap_waiting"))
        return

    heat = (
        alt.Chart(region_map)
        .mark_rect(cornerRadius=6)
        .encode(
            x=alt.X("cart_x", axis=None),
            y=alt.Y("cart_y", axis=None),
            color=alt.Color(
                "processed_percent",
                title="Processed precincts %",
                scale=alt.Scale(scheme="blues"),
            ),
            tooltip=[
                alt.Tooltip("region", title="Region"),
                alt.Tooltip("processed_percent", title="Processed", format=".1f"),
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(heat, use_container_width=True)
    st.caption(t("heatmap_caption"))


def render_abroad_vote_lens(t) -> None:
    st.markdown(f"### {t('abroad_vote_lens')}")
    st.info(t("abroad_placeholder"))


def render_wasted_vote_meter(
    parties_df: pd.DataFrame, summary: Dict[str, Any], t
) -> None:
    st.markdown(f"### {t('wasted_vote_meter')}")
    if parties_df.empty:
        st.info(t("wasted_waiting"))
        return

    wasted = parties_df[parties_df["vote_share"] < parties_df["threshold"]]
    wasted_votes = wasted["votes"].sum()
    total_valid = summary.get("valid_votes")
    if not total_valid or total_valid == 0:
        wasted_pct = None
    else:
        wasted_pct = 100.0 * wasted_votes / total_valid

    cols = st.columns(3)
    cols[0].metric(t("wasted_votes_metric"), fmt_number(int(wasted_votes)))
    cols[1].metric(t("share_of_valid"), fmt_percent(wasted_pct))
    cols[2].metric(t("wasted_parties"), len(wasted))

    if not wasted.empty:
        st.dataframe(
            wasted[["party", "votes", "vote_share", "threshold"]],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(t("wasted_caption"))


def render_paths_to_majority(parties_df: pd.DataFrame, t) -> None:
    st.markdown(f"### {t('paths_to_majority')}")
    viable = parties_df[parties_df["mandates"] > 0]
    if viable.empty:
        st.info(t("paths_waiting"))
        return

    combos: List[Dict[str, Any]] = []
    party_records = viable[["party", "mandates"]].to_dict(orient="records")
    max_parties = min(5, len(party_records))
    for r in range(2, max_parties + 1):
        for combo in combinations(party_records, r):
            seats = sum(item["mandates"] for item in combo)
            if seats < MAJORITY_THRESHOLD:
                continue
            parties = [item["party"] for item in combo]
            minimal = all(
                (seats - item["mandates"]) < MAJORITY_THRESHOLD for item in combo
            )
            ideology_scores = [PARTY_POSITION.get(party, 5) for party in parties]
            ideology_spread = max(ideology_scores) - min(ideology_scores)
            compatibility = HISTORICAL_COMPATIBILITY.get(frozenset(parties), "unknown")
            combos.append(
                {
                    "parties": " + ".join(parties),
                    "seats": seats,
                    "minimal": minimal,
                    "ideology_gap": ideology_spread,
                    "compatibility": compatibility,
                }
            )

    if not combos:
        st.info(t("paths_none"))
        return

    combos_df = pd.DataFrame(combos)
    combos_df.sort_values(
        ["seats", "ideology_gap", "minimal"],
        ascending=[True, True, False],
        inplace=True,
    )
    combos_df = combos_df.head(10)
    st.dataframe(
        combos_df,
        hide_index=True,
        use_container_width=True,
    )
    st.caption(t("paths_caption"))


def render_seats_by_region_placeholder(t) -> None:
    st.markdown(f"### {t('seats_by_region')}")
    st.info(t("seats_by_region_placeholder"))


def render_preference_leaderboard_placeholder(t) -> None:
    st.markdown(f"### {t('preference_leaderboard')}")
    st.info(t("preference_placeholder"))


def render_methodology_section(t) -> None:
    with st.expander("Methodology · Metodika"):
        st.markdown(
            """
**EN**
- Seats are allocated via the D'Hondt method across 14 regions (26 mandátových obvodů) with a chamber size of 200.
- Legal thresholds: 5 % for single parties, 8 % for two-party coalitions, 11 % for 3+ party coalitions (law 350/2021 Sb.).
- Preferential votes (kroužky) can reorder candidate lists when ≥5 % of voters circle an individual.
- Official methodology: [ČSÚ (EN)](https://www.volby.cz/opendata/).

**CZ**
- Mandáty se přepočítávají d'Hondtovou metodou v rámci 14 krajů (26 mandátových obvodů), celkem 200 křesel.
- Zákonné klauzule: 5 % pro samostatné subjekty, 8 % pro koalice dvou, 11 % pro koalice tří a více stran (zákon 350/2021 Sb.).
- Preferenční hlasy (kroužky) mohou přeskupit kandidátky při ≥5 % hlasů pro kandidáta.
- Oficiální metodika: [ČSÚ (CZ)](https://www.volby.cz/opendata/).
            """
        )


def render_uncertainty_panel(
    summary: Dict[str, Any], seats_df: pd.DataFrame, t
) -> None:
    processed_pct = summary.get("wards_processed_percent")
    if processed_pct is None:
        return
    if processed_pct >= 99.9:
        st.success(t("uncertainty_full"))
        return
    outstanding = max(0.0, 100.0 - processed_pct)
    swing = max(1, int(round(outstanding / 4)))
    st.warning(t("uncertainty_partial", processed=processed_pct, swing=swing))


def render_share_tools(language_code: str, t) -> None:
    st.markdown(f"### {t('share_tools_header')}")
    coalition = st.session_state.get("coalition_selection", [])
    share_params = {"lang": language_code}
    if coalition:
        share_params["coalition"] = ",".join(coalition)
    share_query = "?" + urlencode(share_params)
    st.text_input(t("share_tools_caption"), share_query)
    if st.button(t("share_tools_button")):
        st.experimental_set_query_params(**share_params)


def render_downloads(
    parties_df: pd.DataFrame, regions_df: pd.DataFrame, effective_year: int, t
) -> None:
    with st.expander(t("download_data")):
        if not parties_df.empty:
            csv_bytes = parties_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=t("download_parties"),
                data=csv_bytes,
                file_name=f"volby-parties-{effective_year}.csv",
                mime="text/csv",
            )
        if not regions_df.empty:
            regions_csv = regions_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=t("download_regions"),
                data=regions_csv,
                file_name=f"volby-regions-{effective_year}.csv",
                mime="text/csv",
            )


def main() -> None:
    language_choice = st.sidebar.radio(
        "Language / Jazyk", list(LANGUAGE_OPTIONS.keys()), index=0
    )
    language_code = LANGUAGE_OPTIONS.get(language_choice, "cs")
    t = get_translator(language_code)

    with st.sidebar:
        st.header(t("data_options"))
        primary_year = st.selectbox(t("primary_year"), [PRIMARY_YEAR], index=0)
        allow_fallback = st.checkbox(
            t(
                "fallback_option",
                fallback_year=FALLBACK_YEAR,
                primary_year=PRIMARY_YEAR,
            ),
            value=True,
        )
        fallback_year: Optional[int] = FALLBACK_YEAR if allow_fallback else None

    try:
        dataset = load_election_dataset(primary_year, fallback_year)
    except ElectionDataUnavailable:
        st.error(
            "Official election results are not yet published. Enable the fallback option to preview the latest available dataset."
        )
        return

    metadata = dataset.get("metadata", {})
    summary = dataset.get("summary", {})
    parties_raw = dataset.get("parties", [])
    seats_raw = dataset.get("seats", [])
    regions_raw = dataset.get("regions", [])

    effective_year = metadata.get("effective_year", metadata.get("year", primary_year))
    requested_year = metadata.get("requested_year", primary_year)
    if metadata.get("fallback_used"):
        st.warning(
            t(
                "fallback_notice",
                requested_year=requested_year,
                effective_year=effective_year,
            )
        )
    else:
        st.success(t("results_loaded", year=effective_year))

    reference_dataset = load_reference_dataset(BASELINE_YEAR)
    baseline_parties = reference_dataset.get("parties", []) if reference_dataset else []
    baseline_seats = reference_dataset.get("seats", []) if reference_dataset else []

    parties_df = make_parties_dataframe(
        parties_raw, seats_raw, effective_year=effective_year
    )
    seats_df = make_seats_dataframe(seats_raw, effective_year=effective_year)
    regions_df = (
        pd.DataFrame([vars(region) for region in regions_raw])
        if regions_raw
        else pd.DataFrame()
    )
    if not regions_df.empty:
        regions_df.rename(
            columns={
                "region_name": "region",
                "leading_party": "leading_party",
                "leading_percent": "leading_percent",
                "votes": "votes",
                "processed_percent": "processed_percent",
                "detail_url": "detail_url",
            },
            inplace=True,
        )
        for column in ("leading_percent", "processed_percent", "votes"):
            if column in regions_df.columns:
                regions_df[column] = pd.to_numeric(regions_df[column], errors="coerce")
        if effective_year and effective_year >= 2025:
            if "leading_party" in regions_df.columns:
                regions_df["leading_party"] = regions_df["leading_party"].apply(
                    canonical_party_name
                )

    baseline_df = make_parties_dataframe(
        baseline_parties, baseline_seats, effective_year=BASELINE_YEAR
    )
    parties_df = apply_baseline_swing(parties_df, baseline_df)

    st.title(t("title"))
    st.subheader(t("subtitle", year=effective_year))

    render_headline_bar(metadata, summary, parties_df, t)
    st.caption(build_provenance_badge(metadata, "provenance_scope_headline", t))

    overview_tab, maps_tab, deep_dives_tab, clarity_tab = st.tabs(
        ["Live overview", "Maps & geography", "Deep dives", "Clarity & trust"]
    )

    with overview_tab:
        render_majority_tracker(seats_df, t)
        st.caption(build_provenance_badge(metadata, "provenance_scope_majority", t))
        render_hemicycle(seats_df, parties_df, regions_df, t)
        st.divider()
        render_coalition_builder(parties_df, t)
        st.divider()
        render_threshold_watchlist(parties_df, t)
        st.divider()
        render_vote_share_section(parties_df, t)
        st.caption(build_provenance_badge(metadata, "provenance_scope_votes", t))

    with maps_tab:
        region_map_df = render_region_map(parties_df, regions_df, t)
        st.caption(build_provenance_badge(metadata, "provenance_scope_regions", t))
        st.divider()
        render_turnout_cartogram(region_map_df, t)
        st.divider()
        render_abroad_vote_lens(t)

    with deep_dives_tab:
        render_seats_by_region_placeholder(t)
        st.divider()
        render_preference_leaderboard_placeholder(t)
        st.divider()
        render_wasted_vote_meter(parties_df, summary, t)
        st.caption(build_provenance_badge(metadata, "provenance_scope_deepdive", t))
        st.divider()
        render_paths_to_majority(parties_df, t)

    with clarity_tab:
        render_uncertainty_panel(summary, seats_df, t)
        st.divider()
        render_methodology_section(t)
        st.divider()
        render_share_tools(language_code, t)

    render_downloads(parties_df, regions_df, effective_year, t)

    st.caption(t("source_footer"))


if __name__ == "__main__":
    main()
