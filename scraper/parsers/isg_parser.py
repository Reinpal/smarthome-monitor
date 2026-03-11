"""HTML parser for Stiebel Eltron ISG web pages.

Extracts key-value pairs from <table class="info"> elements,
handling German number formats, units, and boolean status indicators.
"""

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Pattern to extract numeric value and unit from strings like "24,4°C", "7,11bar", "15,9l/min"
VALUE_PATTERN = re.compile(
    r"^([+-]?\d+(?:[.,]\d+)?)\s*(°C|bar|mbar|V|A|Hz|kW|KWh|kWh|MWh|l/min|%|h|min)?$"
)

# Image file patterns for boolean status indicators
STATUS_ON_PATTERN = "symbol_an"
STATUS_OFF_PATTERN = "symbol_aus"


@dataclass
class ParsedValue:
    """A single parsed value from the ISG page."""

    section: str  # Table header, e.g. "PROZESSDATEN"
    key: str  # Row label, e.g. "VORLAUFTEMPERATUR"
    raw_value: str  # Original text value
    numeric_value: float | None  # Parsed numeric value (None for booleans/text)
    unit: str  # Unit string, e.g. "°C", "bar", ""
    is_boolean: bool  # True if this is an on/off status indicator
    boolean_value: bool | None  # True/False for status, None for non-boolean


def _normalize_section_name(name: str) -> str:
    """Normalize a section header for use as a metric namespace.

    Converts e.g. 'ELEKTRISCHE NACHERWÄRMUNG' -> 'elektrische_nacherwaermung'
    """
    name = name.strip().lower()
    # Replace German umlauts
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for char, replacement in replacements.items():
        name = name.replace(char, replacement)
    # Replace spaces and non-alphanumeric with underscores
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def _normalize_key_name(name: str) -> str:
    """Normalize a key name for use as a metric name.

    Converts e.g. 'VORLAUFTEMPERATUR' -> 'vorlauftemperatur'
    Converts e.g. 'VD HEIZEN TAG' -> 'vd_heizen_tag'
    """
    return _normalize_section_name(name)


def _parse_numeric_value(text: str) -> tuple[float | None, str]:
    """Parse a German-formatted numeric value with unit.

    Args:
        text: Raw text like "24,4°C" or "7,11bar"

    Returns:
        Tuple of (numeric_value, unit). numeric_value is None if not parseable.
    """
    text = text.strip()
    match = VALUE_PATTERN.match(text)
    if match:
        number_str = match.group(1).replace(",", ".")
        unit = match.group(2) or ""
        # Normalize unit
        if unit == "KWh":
            unit = "kWh"
        try:
            return float(number_str), unit
        except ValueError:
            return None, ""

    # Try pure integer (e.g. "637" for compressor starts)
    try:
        return float(int(text)), ""
    except ValueError:
        return None, ""


def _check_boolean_status(td_element) -> tuple[bool, bool | None]:
    """Check if a table cell contains a boolean status indicator (image).

    Args:
        td_element: BeautifulSoup Tag for the <td> element

    Returns:
        Tuple of (is_boolean, boolean_value)
    """
    img = td_element.find("img")
    if img and img.get("src"):
        src = img["src"]
        if STATUS_ON_PATTERN in src:
            return True, True
        if STATUS_OFF_PATTERN in src:
            return True, False
    return False, None


def parse_isg_page(html: str, page_name: str) -> list[ParsedValue]:
    """Parse an ISG HTML page and extract all data points.

    Args:
        html: Raw HTML content of the page
        page_name: Name of the page for logging purposes

    Returns:
        List of ParsedValue objects
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    tables = soup.find_all("table", class_="info")
    logger.debug("Found %d tables on page '%s'", len(tables), page_name)

    for table in tables:
        # Get section header from <th>
        header_th = table.find("th")
        if not header_th:
            logger.warning("Table without header on page '%s', skipping", page_name)
            continue

        section = header_th.get_text(strip=True)

        # Get all data rows
        rows = table.find_all("tr")
        for row in rows:
            key_td = row.find("td", class_="key")
            value_td = row.find("td", class_="value")
            if not key_td or not value_td:
                continue

            key = key_td.get_text(strip=True)
            raw_value = value_td.get_text(strip=True)

            # Check for boolean status (image-based)
            is_boolean, boolean_value = _check_boolean_status(value_td)

            if is_boolean:
                results.append(
                    ParsedValue(
                        section=section,
                        key=key,
                        raw_value="ON" if boolean_value else "OFF",
                        numeric_value=1.0 if boolean_value else 0.0,
                        unit="",
                        is_boolean=True,
                        boolean_value=boolean_value,
                    )
                )
            else:
                # Check for text-only values like "Aus"
                numeric_value, unit = _parse_numeric_value(raw_value)

                # Handle special text values
                if numeric_value is None and raw_value.lower() == "aus":
                    numeric_value = 0.0
                    is_boolean = True
                    boolean_value = False
                elif numeric_value is None and raw_value.lower() in ("ein", "an"):
                    numeric_value = 1.0
                    is_boolean = True
                    boolean_value = True

                results.append(
                    ParsedValue(
                        section=section,
                        key=key,
                        raw_value=raw_value,
                        numeric_value=numeric_value,
                        unit=unit,
                        is_boolean=is_boolean,
                        boolean_value=boolean_value,
                    )
                )

    logger.info(
        "Parsed %d values from page '%s' (%d tables)",
        len(results),
        page_name,
        len(tables),
    )
    return results


def build_metric_name(page_name: str, section: str, key: str) -> str:
    """Build a fully qualified metric name from page, section and key.

    Example: waermepumpe.prozessdaten.vorlauftemperatur
    """
    norm_section = _normalize_section_name(section)
    norm_key = _normalize_key_name(key)
    return f"heatpump.{page_name}.{norm_section}.{norm_key}"
