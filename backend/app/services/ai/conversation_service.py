import re
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

from app.schemas.ai import (
    AIConversationRequest,
    AIConversationResponse,
    ConversationMessage,
    RequirementDraft,
)
from app.core.config import get_settings
from app.core.request_context import request_id_context
from app.services.ai.base import AIProvider
from app.services.ai.price_understanding import (
    SUPPORTED_CURRENCY_CODES,
    understand_price,
)

_ESSENTIAL_QUESTIONS = {
    "product_name": "What product are you looking for?",
    "capacity": "What capacity do you need for the tank?",
    "quantity": "How many do you need?",
    "unit": "What unit should that quantity use (for example, pieces or units)?",
    "minimum_target_unit_price": "What price or budget should I use for the product?",
    "maximum_target_unit_price": "What price or budget should I use for the product?",
    "price": "What price or budget should I use for the product?",
    "price_clarification": "Could you clarify the price or budget you mean?",
    "currency": "Which currency should I use for that amount (for example, INR or USD)?",
}

_PRODUCT_PATTERNS = (
    re.compile(
        r"\b(?:the\s+)?product\s+is\s+(?:an?\s+|the\s+)?(.+?)"
        r"(?=\s+(?:with\b|capacity\s+of|for\s+my|"
        r"for\s+use|made\s+of|made\s+from|it\s+should|i\s+need\s+\d)|[.,;]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i\s+need|i(?:'m| am)\s+looking\s+for|looking\s+for)\s+"
        r"(?:an?\s+|the\s+)?(.+?)"
        r"(?=\s+(?:for\b|with\b|and\b|it\s+should|it\s+must|"
        r"my\s+budget|budget\s+of|i\s+prefer|i\s+want|"
        r"capacity\s+of|made\s+of|made\s+from)|[.,;]|$)",
        re.IGNORECASE,
    ),
)
_CAPACITY_PATTERN = re.compile(
    r"\bcapacity\s+(?:of\s+)?(\d+(?:[.,]\d+)?)\s*"
    r"(litres?|liters?|gallons?|ml|millilitres?|milliliters?|"
    r"m3|cubic\s+met(?:er|re)s?)\b"
    r"|\b(\d+(?:[.,]\d+)?)\s*"
    r"(litres?|liters?|gallons?|ml|millilitres?|milliliters?|"
    r"m3|cubic\s+met(?:er|re)s?)"
    r"(?:\s+of\s+(?:storage\s+)?capacity|\s+capacity)?\b",
    re.IGNORECASE,
)
_BATTERY_CAPACITY_PATTERN = re.compile(
    r"\b(?:battery\s+)?capacity\b\s*(?:(?:of|is|:|should\s+be)\s*)?"
    r"(?:(?:at\s+least|minimum(?:\s+of)?|min\.?\s*)\s*)?"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*mAh\b"
    r"(?:\s*(?:or\s+higher|or\s+more|or\s+above|and\s+above))?",
    re.IGNORECASE,
)
_NUMBER_WORD_PATTERN = (
    r"(?:\d+(?:[.,]\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety)"
)
_QUANTITY_UNIT_PATTERN = (
    r"(?:units?|pieces?|pcs|items?|sets?|packs?|boxes?|"
    r"laptops?|phones?|mobile\s+phones?|monitors?|tanks?|watches?|"
    r"chairs?|tables?|cameras?|printers?|hair\s+bands?|kg|kilograms?|g|grams?|"
    r"tonnes?|tons?|litres?|liters?|ml|millilitres?|milliliters?|"
    r"met(?:er|re)s?|cm|mm|ft|feet|inches?|GB|TB|V|volts?|W|kW|watts?|"
    r"MHz|GHz|Hz|mAh|Ah|%)"
)
_QUANTITY_PATTERN = re.compile(
    r"\b(?:i\s+need|need|quantity(?:\s+of)?|require(?:ment)?(?:\s+of)?|"
    r"order(?:\s+quantity)?(?:\s+of)?)\s*(?:just\s+)?(?:is\s+|of\s+)?"
    rf"(?P<number>{_NUMBER_WORD_PATTERN})\s*"
    rf"(?P<unit>{_QUANTITY_UNIT_PATTERN}|[a-z][a-z-]*(?:\s+[a-z][a-z-]*)?)?\b",
    re.IGNORECASE,
)
_TOTAL_BUDGET_QUANTITY_PATTERN = re.compile(
    rf"\bfor\s+(?P<number>{_NUMBER_WORD_PATTERN})\s+"
    r"(?P<unit>units?|pieces?|items?|pcs)\b",
    re.IGNORECASE,
)
_SHORT_QUANTITY_PATTERN = re.compile(
    rf"^\s*(?:just\s+)?({_NUMBER_WORD_PATTERN})"
    r"(?:\s+(laptops?|phones?|mobile\s+phones?|monitors?|tanks?|watches?|"
    r"units?|pieces?|pcs|items?|sets?|boxes?|hair\s+bands?))?"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)
_SHORT_UNIT_PATTERN = re.compile(
    r"^\s*(units?|pieces?|pcs|items?|sets?|packs?|boxes?|laptops?|phones?|"
    r"mobile\s+phones?|monitors?|tanks?|watches?|chairs?|tables?|"
    r"kg|kilograms?|litres?|liters?)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
}
_COUNTABLE_PRODUCT_PATTERN = re.compile(
    r"\b(laptops?|phones?|mobile\s+phones?|monitors?|displays?|tanks?|hair\s+bands?|"
    r"smartwatches?|watches?|chairs?|tables?|cameras?|printers?|units?|pieces?)\b",
    re.IGNORECASE,
)
_PRODUCT_CATEGORIES = {
    "laptop": "Computers",
    "phone": "Mobile Phones",
    "monitor": "Computer Monitors",
    "tank": "Industrial Storage",
}
_NON_PRODUCT_TERMS = {
    "unit",
    "units",
    "piece",
    "pieces",
    "pc",
    "pcs",
    "item",
    "items",
    "set",
    "sets",
    "box",
    "boxes",
    "one",
    "two",
    "three",
    "four",
    "five",
}
_MATERIAL_PATTERN = re.compile(
    r"\b(?:SS\s*[-]?\s*\d{3,4}|stainless\s+steel(?:\s+(?:grade\s*)?\d{3,4})?)\b",
    re.IGNORECASE,
)
_BUDGET_PATTERN = re.compile(
    r"\b(?:maximum\s+budget|max(?:imum)?\s+budget|budget|budget\s+of|"
    r"under|up\s+to)\s*(?::|of)?\s*(?:is\s*)?(₹|Rs\.?|INR|[$€£]\s*)?"
    r"\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(USD|EUR|GBP|INR|dollars?|euros?|pounds?)?\b",
    re.IGNORECASE,
)
_RAM_PATTERN = re.compile(
    r"\b(?:at\s+least|minimum(?:\s+of)?|min\.?\s*)?\s*"
    r"(\d+(?:[.,]\d+)?)\s*(GB|TB)\s+(?:of\s+)?RAM\b",
    re.IGNORECASE,
)
_STORAGE_PATTERN = re.compile(
    r"\b(?:at\s+least|minimum(?:\s+of)?|min\.?\s*)?\s*"
    r"(\d+(?:[.,]\d+)?)\s*(GB|TB)\s*(SSD|HDD|NVMe)\b",
    re.IGNORECASE,
)
_VOLTAGE_PATTERN = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(V|volts?)\b", re.IGNORECASE
)
_POWER_PATTERN = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(W|kW|watts?|kilowatts?)\b", re.IGNORECASE
)
_PREFERENCE_PATTERN = re.compile(
    r"\b(?:i\s+(?:also\s+)?prefer|prefer(?:red)?\s+brands?(?:\s+are)?|brand\s+preference\s*(?:is|:))"
    r"\s+([a-z0-9][a-z0-9 ,/&-]*?)(?=[.;]|$|\s+(?:good\s+battery|a\s+lightweight|"
    r"lightweight|backlit|and\s+(?:good\s+battery|a\s+lightweight|backlit)))",
    re.IGNORECASE,
)
_BATTERY_PATTERN = re.compile(r"\b(good|long|extended)\s+battery\s+life\b", re.IGNORECASE)
_WEIGHT_PATTERN = re.compile(r"\b(lightweight|light[- ]weight)\s+(?:design|build)?", re.IGNORECASE)
_KEYBOARD_PATTERN = re.compile(r"\b(backlit)\s+keyboard\b", re.IGNORECASE)
_APPLICATION_PATTERN = re.compile(
    r"\b(?:for\s+my|for\s+use\s+(?:in|at)|application\s*(?:is|:)|"
    r"for\s+(?!a\b|an\b|the\b|different\s+hairstyles?\b))\s*"
    r"([a-z][a-z0-9 ,&/'-]{1,200}?)(?=[.;!?]|$|\s+(?:it\s+should|i\s+need|"
    r"my\s+budget|i\s+prefer))",
    re.IGNORECASE,
)
_SPECIFICATION_CLAUSE_PATTERN = re.compile(
    r"\b(?:it\s+)?(?:should|must|needs?\s+to|has\s+to)\s+"
    r"(?:(?:have|include|offer|support|provide|be)\s+)?"
    r"|\b(?:with|including|featuring|equipped\s+with)\s+"
    r"|\bi\s+want\s+"
    r"|\bi\s+need\s+(?=(?:an?\s+)?(?:intel|amd|snapdragon|processor|cpu|display|screen|resolution)\b)",
    re.IGNORECASE,
)
_DISPLAY_SIZE_PATTERN = re.compile(
    r"\b(?P<size>\d+(?:[.,]\d+)?)\s*[- ]?inch(?:es)?\b"
    r"(?=[^.;!?]{0,60}\bdisplay\b)",
    re.IGNORECASE,
)
_DISPLAY_TYPE_PATTERN = re.compile(
    r"\b(?P<display_type>FHD|QHD|UHD|HD)\s*(?P<plus>\+?)\s+display\b",
    re.IGNORECASE,
)
_DELL_XPS_PATTERN = re.compile(
    r"\b(?P<brand>Dell)\s+(?P<model>XPS\s*\d+)\s+(?P<product_type>laptop)\b",
    re.IGNORECASE,
)
_USE_CASE_PREFERENCE_PATTERN = re.compile(
    r"\bfor\s+(?P<use_case>(?:everyday\s+)?professional\s+use)\b",
    re.IGNORECASE,
)
_RECOMMENDATION_STYLE_PATTERN = re.compile(
    r"\b(?:similar|closest|more|other)\s+alternatives?\b|"
    r"\balternatives?\s+(?:similar|like)\b",
    re.IGNORECASE,
)
_NEGATIVE_REQUIREMENT_PATTERNS = (
    re.compile(r"\bwithout\s+(?P<value>[^.;!?]+)", re.IGNORECASE),
    re.compile(r"\b(?:must|should)\s+not\s+(?P<value>[^.;!?]+)", re.IGNORECASE),
    re.compile(r"\bfree\s+from\s+(?P<value>[^.;!?]+)", re.IGNORECASE),
    re.compile(
        r"\bno\s+(?!(?:less|more)\s+than\b)(?P<value>[^.;!?]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bdoes\s+not\s+cause\s+(?P<value>[^.;!?]+)", re.IGNORECASE),
)
_ATTRIBUTE_PREFERENCE_PATTERN = re.compile(
    r"\b(?:i\s+(?:also\s+)?prefer|preferred|preference\s+for|colou?rs?\s+(?:are|include)|"
    r"styles?\s+(?:such\s+as|include|are)|materials?\s+(?:are|include)|"
    r"made\s+of|made\s+from|available\s+in)\s+(?P<values>[^.;!?]+)",
    re.IGNORECASE,
)
_BUDGET_CONTEXT_PATTERN = re.compile(
    r"\b(?:my\s+)?(?:maximum\s+)?budget(?:\s+is|\s+of)?\s*:?\s*|"
    r"\b(?:up\s+to|at\s+least|under|over)\s+(?=(?:₹|Rs\.?|INR|[$€£]|\d))|"
    r"\bbetween\s+(?=(?:₹|Rs\.?|INR|[$€£]|\d))",
    re.IGNORECASE,
)
_BUDGET_AMOUNT_PATTERN = re.compile(
    r"(?P<currency>₹|Rs\.?|INR|USD|EUR|GBP|[$€£])?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_BUDGET_PER_UNIT_PATTERN = re.compile(
    r"\bper\s+(?P<unit>[a-z][a-z -]*?)(?=[.,;!?]|$)",
    re.IGNORECASE,
)
_VARIETY_QUANTITY_PATTERN = re.compile(
    r"\b(?P<quantity>\d+)\s+(?P<unit>packets?|packs?|boxes?|sets?)\s+each\s+of\s+"
    r"(?P<varieties>[^.;!?]+?)(?:\s+seeds?)?(?=,?\s+(?:making|for)\s+a\s+total|[.;!?]|$)",
    re.IGNORECASE,
)
_TOTAL_QUANTITY_PATTERN = re.compile(
    r"\b(?:making\s+)?(?:a\s+)?total\s+of\s+(?P<quantity>\d+)\s+"
    r"(?P<unit>units?|pieces?|packets?|packs?|boxes?|sets?)\b",
    re.IGNORECASE,
)
_SPECIFICATION_SUFFIXES = (
    ("water resistance", "water_resistance"),
    ("germination rate", "germination_rate"),
    ("germination", "germination_rate"),
    ("disease resistance", "disease_resistance"),
    ("disease-resistant", "disease_resistance"),
    ("hybrid", "seed_characteristics"),
    ("climate resistance", "climate_resistance"),
    ("climate-resistant", "climate_resistance"),
    ("moisture resistance", "moisture_resistance"),
    ("expiry information", "expiry_information"),
    ("expiry", "expiry_information"),
    ("germination information", "germination_information"),
    ("hair damage", "hair_damage"),
    ("secure hold", "secure_hold"),
    ("skin-friendly material", "material"),
    ("elasticity", "elasticity"),
    ("washable", "washability"),
    ("durable", "durability"),
    ("comfortable", "comfort"),
    ("lightweight", "weight"),
    ("battery life", "battery_life"),
    ("heart rate monitoring", "heart_rate_monitoring"),
    ("sleep tracking", "sleep_tracking"),
    ("bluetooth calling", "bluetooth_calling"),
    ("refresh rate", "refresh_rate"),
    ("operating system", "operating_system"),
    ("connectivity", "connectivity"),
    ("resolution", "resolution"),
    ("processor", "processor"),
    ("capacity", "capacity"),
    ("material", "material"),
    ("dimensions", "dimensions"),
    ("voltage", "voltage"),
    ("power", "power"),
    ("weight", "weight"),
    ("finish", "finish"),
    ("color", "color"),
    ("colour", "color"),
    ("case", "case"),
    ("style", "style"),
    ("operation", "operation"),
    ("packaging", "packaging"),
    ("moisture-resistant", "moisture_resistance"),
    ("sealed", "packaging"),
    ("skin-friendly", "material_characteristics"),
    ("stretchable", "material_characteristics"),
    ("soft fabric", "materials"),
    ("cotton", "materials"),
    ("satin", "materials"),
    ("floral", "styles"),
    ("bow", "styles"),
    ("solid colours", "styles"),
    ("solid colors", "styles"),
    ("pastel", "colours"),
    ("display", "display"),
    ("keyboard", "keyboard"),
    ("camera", "camera"),
    ("storage", "storage"),
    ("ram", "ram"),
    ("ssd", "storage"),
    ("hdd", "storage"),
    ("nvme", "storage"),
    ("gps", "gps"),
    ("rating", "rating"),
)
_MEASURED_SPECIFICATION_PATTERN = re.compile(
    r"^(?P<qualifier>(?:(?:at\s+least|at\s+most|no\s+less\s+than|no\s+more\s+than|"
    r"minimum(?:\s+of)?|maximum(?:\s+of)?|up\s+to)\s+)?)"
    r"(?P<value>\d+(?:[.,]\d+)?\s*(?:[a-z%]+))"
    r"(?:\s+of)?\s+(?P<attribute>[a-z][a-z0-9 -]*)$",
    re.IGNORECASE,
)
_DELIVERY_LOCATION_PATTERN = re.compile(
    r"\b(?:deliver(?:y)?\s+to|ship(?:ment)?\s+to|delivery\s+location\s*(?:is|:))"
    r"\s+([a-z0-9][a-z0-9 ,.'-]{1,100}?)(?=[.;]|$|\s+(?:by|before|within))",
    re.IGNORECASE,
)
_DELIVERY_REQUIREMENT_PATTERN = re.compile(
    r"\b(?:(?:deliver(?:y)?\s+)?(?:by|before|within)|"
    r"required\s+delivery\s+date\s*(?:is|:))"
    r"\s+([a-z0-9][a-z0-9 ,.'-]{1,80}?)(?=[.;]|$)",
    re.IGNORECASE,
)


def _normalize_number(value: str) -> str:
    normalized = value.casefold().replace("-", " ").strip()
    if normalized in _NUMBER_WORDS:
        return _NUMBER_WORDS[normalized]
    parts = normalized.split()
    if len(parts) == 2 and parts[0] in _NUMBER_WORDS and parts[1] in _NUMBER_WORDS:
        return str(int(_NUMBER_WORDS[parts[0]]) + int(_NUMBER_WORDS[parts[1]]))
    return normalized.replace(",", ".")


def _merge_application_values(existing: str, new_value: str) -> str:
    values = [
        value.strip()
        for source in (existing, new_value)
        for value in source.split(";")
        if value.strip()
    ]
    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value.casefold() not in seen:
            unique_values.append(value)
            seen.add(value.casefold())
    return "; ".join(unique_values)


def _split_specification_items(value: str, *, split_or: bool = False) -> list[str]:
    delimiter = r"(?<!\d),(?!\s*\d{3}(?:\D|$))\s*(?:and\s+)?|\s+\band\b\s+"
    if split_or:
        delimiter += r"|\s+\bor\b\s+"
    items = re.split(delimiter, value, flags=re.I)
    return [
        re.sub(
            r"^(?:(?:a|an|the|also|and|or)\s+)+",
            "",
            item.strip(" .,:;"),
            flags=re.I,
        )
        for item in items
        if item.strip(" .,:; ")
    ]


def _specification_key_value(item: str) -> tuple[str, str]:
    normalized = item.strip(" .,:;")
    folded = normalized.casefold()
    if folded in {"and", "or", "with", "including", "featuring", "equipped with"}:
        return "", ""
    purchase_quantity = re.fullmatch(
        r"(?:(?:purchase|order|requested)\s+)?quantity\s*"
        r"(?:(?:of|is)\s+|[:=]\s*)?"
        r"(?P<amount>\d+(?:[.,]\d+)?)\s*"
        r"(?P<unit>units?|pieces?|items?|pcs?|laptops?|phones?|sets?|packs?|boxes?)?",
        normalized,
        re.IGNORECASE,
    )
    if purchase_quantity:
        amount = purchase_quantity.group("amount").replace(",", ".")
        unit = purchase_quantity.group("unit")
        value = f"{amount} {unit}" if unit else amount
        return "purchase_quantity", value
    battery_capacity = _BATTERY_CAPACITY_PATTERN.search(normalized)
    if battery_capacity:
        amount = battery_capacity.group("amount").replace(",", "")
        return "battery_capacity", f"at least {amount} mAh"
    if re.search(r"\b(?:without|must\s+not|should\s+not|free\s+from|no\s+)\b", folded):
        return "exclusions", normalized
    for suffix, key in _SPECIFICATION_SUFFIXES:
        match = re.search(rf"\b{re.escape(suffix)}\b", folded)
        if not match:
            continue
        before = normalized[:match.start()].strip()
        after = normalized[match.end():].strip()
        if key == "gps":
            value = normalized
        elif key in {
            "heart_rate_monitoring",
            "sleep_tracking",
            "bluetooth_calling",
            "seed_characteristics",
        } and normalized.casefold() in {
            "heart rate monitoring",
            "sleep tracking",
            "bluetooth calling",
            "hybrid",
        }:
            value = normalized.capitalize() if key == "seed_characteristics" else "required"
        elif suffix in {"battery life", "germination rate"}:
            value = re.sub(r"\s+of$", "", before, flags=re.I) or after
        elif before and after:
            value = f"{before} {after}".strip()
        else:
            value = before or after or "required"
        value = re.sub(r"^(?:a|an|the)\s+", "", value, flags=re.I)
        if key == "washability" and (value.casefold() == "required" or value.casefold() == "washable"):
            value = "Washable"
        elif key == "durability" and (value.casefold() == "required" or value.casefold() == "durable"):
            value = "Durable"
        elif key == "comfort" and (value.casefold() == "required" or value.casefold() == "comfortable"):
            value = "Comfortable"
        elif key == "weight" and (value.casefold() == "required" or value.casefold() == "lightweight"):
            value = "Lightweight"
        elif key in {"styles", "colours", "materials"} and value.casefold() == "required":
            value = normalized
        elif key == "packaging" and value.casefold() == "required":
            value = "Required"
        elif key == "disease_resistance" and value.casefold() == "required":
            value = "Disease-resistant"
        elif key == "disease_resistance" and value.casefold() in {"varieties", "plants", "seeds"}:
            value = "Disease-resistant"
        elif key == "climate_resistance" and value.casefold() in {"required", "characteristics"}:
            value = "Climate-resistant"
        elif key == "climate_resistance" and value.casefold() in {"varieties", "plants", "seeds"}:
            value = "Climate-resistant"
        elif key == "moisture_resistance" and value.casefold() == "packaging":
            value = "Moisture-resistant"
        return key, value
    measured = _MEASURED_SPECIFICATION_PATTERN.fullmatch(folded)
    if measured:
        attribute = measured.group("attribute").strip()
        key = re.sub(r"[^a-z0-9]+", "_", attribute).strip("_")
        value = f"{measured.group('qualifier')}{measured.group('value')}".strip()
        return key[:80], value
    normalized = re.sub(r"^(?:a|an|the|also)\s+", "", normalized, flags=re.I)
    if re.search(r"\bhold(?:s)?\s+hair\s+securely\b", normalized, re.I):
        return "secure_hold", "Holds hair securely"
    if re.search(r"\bstylish\b", normalized, re.I):
        return "style", "Stylish"
    if re.search(r"\bcomfortable\b", normalized, re.I):
        return "comfort", "Comfortable"
    if re.search(r"\b(?:durable|washable|lightweight)\b", normalized, re.I):
        result: dict[str, str] = {}
        for word, key, value in (
            ("durable", "durability", "Durable"),
            ("washable", "washability", "Washable"),
            ("lightweight", "weight", "Lightweight"),
        ):
            if re.search(rf"\b{word}\b", normalized, re.I):
                result[key] = value
        if len(result) == 1:
            key, value = next(iter(result.items()))
            return key, value
    if re.search(r"\bsuitable\s+for\s+(?P<target>[^,;]+)", normalized, re.I):
        target = re.search(r"\bsuitable\s+for\s+(?P<target>[^,;]+)", normalized, re.I)
        if target:
            target_value = target.group("target").strip()
            if "hairstyle" in target_value.casefold():
                return "hairstyle_suitability", f"Suitable for {target_value}"
            return "suitability", f"Suitable for {target_value}"
    if re.fullmatch(r"comfortable", normalized, re.I):
        return "comfort", "Comfortable"
    if re.fullmatch(r"stylish", normalized, re.I):
        return "style", "Stylish"
    key = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    if not key or len(key) > 80:
        return "", ""
    if not re.search(
        r"\d\s*(?:GB|TB|V|volts?|W|kW|watts?|MHz|GHz|Hz|mAh|Ah|%|"
        r"kg|kilograms?|g|grams?|litres?|liters?|ml|meters?|metres?|cm|mm|ft|inches?)"
        r"|at\s+least|at\s+most|minimum|maximum|up\s+to|no\s+less|no\s+more",
        normalized,
        re.I,
    ):
        return key, "required"
    return key[:80], normalized or "required"


def _extract_specification_clauses(
    text: str,
    mandatory_requirements: dict[str, str],
    optional_preferences: dict[str, str],
) -> None:
    labeled_sections = re.compile(
        r"\b(?P<section>mandatory requirements|optional preferences)\s*:\s*"
        r"(?P<items>.*?)(?=\b(?:mandatory requirements|optional preferences)\s*:|[.!?]|$)",
        re.IGNORECASE,
    )
    for section in labeled_sections.finditer(text):
        destination = (
            mandatory_requirements
            if section.group("section").casefold() == "mandatory requirements"
            else optional_preferences
        )
        for item in _split_specification_items(section.group("items")):
            normalized = re.sub(r"\bheart[- ]rate\b", "heart rate", item, flags=re.I)
            folded = normalized.casefold()
            if folded == "bluetooth connectivity":
                key, value = "bluetooth_connectivity", "required"
            elif folded == "android compatibility":
                key, value = "android_compatibility", "required"
            else:
                key, value = _specification_key_value(normalized)
            if key and value:
                destination[key] = _merge_list_values(
                    destination.get(key, ""),
                    value,
                )

    masked_text = labeled_sections.sub(" ", text)
    for pattern in _NEGATIVE_REQUIREMENT_PATTERNS:
        masked_text = pattern.sub(" ", masked_text)
    masked_text = re.sub(
        r"\bmade\s+(?:of|from)\s+[^.;!?]+",
        " ",
        masked_text,
        flags=re.I,
    )
    for clause in _SPECIFICATION_CLAUSE_PATTERN.finditer(masked_text):
        end = re.search(r"(?<!\d)[.!?;]|[!?;]|\.(?!\d)", masked_text[clause.end():])
        clause_end = clause.end() + end.start() if end else len(masked_text)
        content = masked_text[clause.end():clause_end]
        for item in _split_specification_items(content):
            if re.fullmatch(r"[\d,.\s]+", item):
                continue
            if re.search(
                r"\b(?:price|budget|cost|INR|USD|EUR|GBP)\b|"
                r"(?<!\w)Rs\.?(?!\w)|₹|[$€£¥]",
                item,
                re.I,
            ):
                continue
            if re.match(r"\s*made\s+(?:of|from)\b", item, re.I):
                continue
            if re.search(r"\bhave\s+a\s+large\s+storage\s+capacity\b", item, re.I):
                continue
            if re.search(r"\bstylish\b", item, re.I):
                mandatory_requirements["style"] = _merge_list_values(
                    mandatory_requirements.get("style", ""),
                    "Stylish",
                )
            if re.search(r"\bhold(?:s)?\s+hair\s+securely\b", item, re.I):
                mandatory_requirements["secure_hold"] = _merge_list_values(
                    mandatory_requirements.get("secure_hold", ""),
                    "Holds hair securely",
                )
            if re.search(r"\bstylish\b|\bhold(?:s)?\s+hair\s+securely\b", item, re.I):
                continue
            key, value = _specification_key_value(item)
            if key == "display" and (
                _DISPLAY_SIZE_PATTERN.search(item)
                or _DISPLAY_TYPE_PATTERN.search(item)
            ):
                continue
            if not key or not value:
                continue
            if key in mandatory_requirements:
                mandatory_requirements[key] = _merge_list_values(
                    mandatory_requirements[key],
                    value,
                )
            else:
                mandatory_requirements[key] = value


def _merge_list_values(existing: str, incoming: str) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for source in (existing, incoming):
        for value in re.split(r"\s*;\s*", source):
            normalized = value.strip()
            if normalized and normalized.casefold() not in seen:
                values.append(normalized)
                seen.add(normalized.casefold())
    return "; ".join(values)


def _merge_brand_values(existing: str, incoming: str) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for source in (existing, incoming):
        for value in re.split(r"\s*[;,]\s*|\s+\bor\b\s+", source, flags=re.I):
            normalized = re.sub(r"^(?:and|or)\s+", "", value.strip(), flags=re.I)
            if normalized and normalized.casefold() not in seen:
                values.append(normalized)
                seen.add(normalized.casefold())
    return ", ".join(values)


def _extract_negative_requirements(text: str, requirements: dict[str, str]) -> None:
    for pattern in _NEGATIVE_REQUIREMENT_PATTERNS:
        for match in pattern.finditer(text):
            phrase = re.sub(r"\s+", " ", match.group("value")).strip(" ,;.")
            if not phrase:
                continue
            full = match.group(0).strip(" ,;.")
            if "chemical" in phrase.casefold():
                key = "chemical_treatment"
                value = "Must not be treated with harmful chemicals"
            elif "without" in full.casefold():
                key = "exclusions"
                value = _normalize_negative_phrase(phrase, text)
            elif re.match(r"\b(?:must|should)\s+not\b", full, re.I):
                key = "exclusions"
                value = _normalize_negative_phrase(full, text)
            elif re.match(r"\bfree\s+from\b", full, re.I):
                key = "exclusions"
                value = f"Must be free from {phrase}"
            elif re.match(r"\bdoes\s+not\s+cause\b", full, re.I):
                key = "exclusions"
                value = f"Must not cause {phrase}"
            else:
                key = "exclusions"
                value = f"Must not include {phrase}"
            requirements[key] = _merge_list_values(requirements.get(key, ""), value)


def _extract_material_characteristics(text: str, requirements: dict[str, str]) -> None:
    for match in re.finditer(
        r"\bmade\s+(?:of|from)\s+(?P<material>[^.;!?]+)",
        text,
        re.I,
    ):
        value = re.split(
            r"\s+(?:(?:and\s+)?(?:it\s+)?(?:should\s+)?(?:have|include|with))\s+",
            match.group("material"),
            maxsplit=1,
            flags=re.I,
        )[0]
        if re.search(r"\bSS\s*[-]?\s*\d{3,4}\b", value, re.I):
            continue
        value = re.sub(r"\s+material$", "", value.strip(), flags=re.I)
        parts = _split_specification_items(value)
        if parts:
            requirements["material_characteristics"] = _merge_list_values(
                requirements.get("material_characteristics", ""),
                "; ".join(part.capitalize() for part in parts),
            )
        elasticity = re.search(r"\b(?:good\s+)?elasticity\b", match.group("material"), re.I)
        if elasticity:
            requirements["elasticity"] = "Good elasticity" if "good" in elasticity.group(0).casefold() else "Elastic"
    if re.search(r"\bexpiry\s+and\s+germination\s+information\b", text, re.I):
        requirements["expiry_information"] = "Expiry information"
        requirements["germination_information"] = "Germination information"
    packaging = re.search(
        r"\bsealed,\s*moisture-resistant\s+packaging\b",
        text,
        re.I,
    )
    if packaging:
        requirements["packaging"] = _merge_list_values(
            requirements.get("packaging", ""),
            "Sealed; moisture-resistant",
        )


def _normalize_negative_phrase(phrase: str, text: str) -> str:
    if re.search(r"\b(?:hair|hairstyle)\b", text, re.I) and re.search(
        r"\b(?:pull|damage|break)\w*\b", phrase, re.I
    ):
        return "Must not pull hair or cause hair damage"
    if re.search(r"\bharmful\s+chemicals?\b", phrase, re.I):
        return "Must not be treated with harmful chemicals"
    if re.search(r"\birritat\w*\b", phrase, re.I):
        if re.search(r"\bskin\b", phrase, re.I):
            return "Must not irritate skin"
        return "Must not cause skin irritation"
    if re.search(r"\bslip\w*\b", phrase, re.I):
        return "Must not slip"
    if re.match(r"\s*(?:must|should)\s+not\b", phrase, re.I):
        phrase = re.sub(r"^\s*(?:must|should)\s+not\s+", "", phrase, flags=re.I)
    return f"Must not {phrase}"


def _extract_budget(text: str, commercial_constraints: dict[str, str]) -> None:
    contexts = list(_BUDGET_CONTEXT_PATTERN.finditer(text))
    contexts_with_amount = [
        match
        for match in contexts
        if _BUDGET_AMOUNT_PATTERN.search(text[match.end():])
    ]
    context = next(
        (match for match in contexts_with_amount if "budget" in match.group(0).casefold()),
        contexts_with_amount[0] if contexts_with_amount else None,
    )
    if context:
        segment = text[context.end():]
    else:
        price_context = re.search(
            r"(?:₹|Rs\.?|INR|USD|EUR|GBP|[$€£])\s*\d[\d,]*(?:\.\d{1,2})?"
            r"\s+per\s+[a-z]",
            text,
            re.I,
        )
        if not price_context:
            return
        segment = text[price_context.start():]
    segment = re.split(r"[.;!?]", segment, maxsplit=1)[0].strip()
    if not segment:
        return

    currency_match = re.search(r"₹|Rs\.?|INR|USD|EUR|GBP|[$€£]", segment, re.I)
    currency = None
    if currency_match:
        currency = {
            "₹": "INR",
            "rs": "INR",
            "rs.": "INR",
            "inr": "INR",
            "$": "USD",
            "usd": "USD",
            "€": "EUR",
            "eur": "EUR",
            "£": "GBP",
            "gbp": "GBP",
        }.get(currency_match.group(0).casefold())
    amounts = list(_BUDGET_AMOUNT_PATTERN.finditer(segment))
    if not amounts:
        return

    first_amount = amounts[0]
    measurement_suffix = (
        r"\s*(?:GB|TB|V|volts?|W|kW|watts?|MHz|GHz|Hz|mAh|Ah|kg|kilograms?|"
        r"g|grams?|litres?|liters?|ml|meters?|metres?|cm|mm|ft|inches?)\b"
    )
    if re.match(measurement_suffix, segment[first_amount.end():], re.I):
        return
    range_amount = next(
        (
            candidate
            for candidate in amounts[1:]
            if re.fullmatch(
                r"\s*(?:to|and|[-–—])\s*",
                segment[first_amount.end():candidate.start()],
                re.I,
            )
            and not re.match(measurement_suffix, segment[candidate.end():], re.I)
        ),
        None,
    )
    amount_values = [first_amount.group("amount").replace(",", "")]
    if range_amount:
        amount_values.append(range_amount.group("amount").replace(",", ""))
    segment_prefix = segment[:amounts[0].start()].casefold()
    mode_text = f"{context.group(0) if context else ''} {segment_prefix}".casefold()
    if re.search(r"\b(?:at\s+least|minimum|min(?:imum)?\s+of)\b", mode_text):
        budget_value = f"{amount_values[0]} minimum"
    elif re.search(r"\b(?:up\s+to|at\s+most|maximum|max|under|below)\b", mode_text):
        modifier = re.search(r"\b(?:under|below)\b", mode_text)
        budget_value = f"{modifier.group(0) if modifier else 'up to'} {amount_values[0]}"
    elif re.search(r"\b(?:over|above|more\s+than)\b", mode_text):
        budget_value = f"over {amount_values[0]}"
    elif len(amount_values) > 1:
        budget_value = f"{amount_values[0]}–{amount_values[1]}"
    else:
        budget_value = amount_values[0]

    per_unit = _BUDGET_PER_UNIT_PATTERN.search(segment)
    unit_suffix = f" per {per_unit.group('unit').strip()}" if per_unit else ""
    commercial_constraints["budget"] = f"{budget_value}{unit_suffix}".strip()
    if currency:
        commercial_constraints["currency"] = currency


def _extract_variety_quantities(text: str, commercial_constraints: dict[str, str]) -> None:
    match = _VARIETY_QUANTITY_PATTERN.search(text)
    if not match:
        return
    varieties = _split_specification_items(match.group("varieties"), split_or=True)
    quantity = match.group("quantity")
    unit = match.group("unit").casefold()
    commercial_constraints["variety_quantities"] = "; ".join(
        f"{variety.rstrip('s').capitalize()}: {quantity} {unit}"
        for variety in varieties
    )
    commercial_constraints.setdefault("quantity", quantity)
    commercial_constraints.setdefault("unit", unit)
    total = _TOTAL_QUANTITY_PATTERN.search(text)
    if total:
        commercial_constraints["quantity"] = total.group("quantity")
        commercial_constraints["unit"] = total.group("unit").casefold()


def _extract_classified_preferences(text: str, preferences: dict[str, str]) -> None:
    list_patterns = (
        ("styles", re.compile(
            r"\b(?:i\s+(?:also\s+)?prefer|preferred)\s+" 
            r"(?P<values>[^.;!?]+?)\s+styles?\b", re.I
        )),
        ("styles", re.compile(
            r"\b(?:different\s+)?styles?\s+(?:such\s+as|including|like|are)\s+"
            r"(?P<values>[^.;!?]+?)(?=\.|;|!|\?|$)", re.I
        )),
        ("colours", re.compile(
            r"\b(?:i\s+(?:also\s+)?prefer|preferred)\s+(?P<values>[^.;!?]*?)\s+colou?rs?\b", re.I
        )),
        ("materials", re.compile(
            r"\b(?:my\s+)?preferred\s+materials?\s+(?:are|include)\s+"
            r"(?P<values>[^.;!?]+)", re.I
        )),
        ("materials", re.compile(
            r"\bmaterials?\s+(?:such\s+as|including)\s+(?P<values>[^.;!?]+)", re.I
        )),
    )
    for key, pattern in list_patterns:
        for match in pattern.finditer(text):
            raw_values = re.sub(
                r"^(?:i\s+prefer|i\s+like|my\s+preferred)\s+",
                "",
                match.group("values"),
                flags=re.I,
            )
            if key == "styles" and raw_values.casefold().strip() == "different":
                continue
            values = _split_specification_items(raw_values, split_or=True)
            if key == "colours":
                if re.search(r"\bstyles?\b", raw_values, re.I):
                    color_list = re.search(
                        r"\bstyles?\b.*?(?:,\s*|\band\s+)(?P<values>[^.;!?]+)$",
                        raw_values,
                        re.I,
                    )
                    if not color_list:
                        continue
                    values = _split_specification_items(
                        color_list.group("values"),
                        split_or=True,
                    )
                values = [
                    re.sub(r"\s+(?:colou?rs?)\b.*$", "", value, flags=re.I).strip()
                    for value in values
                ]
                values = [value for value in values if value]
            if values:
                formatted = "; ".join(value[:1].upper() + value[1:] for value in values)
                preferences[key] = _merge_list_values(
                    preferences.get(key, ""),
                    formatted,
                )

    for match in re.finditer(
        r"\b(?:i\s+(?:also\s+)?prefer|preferred)\s+(?P<values>[^.;!?]+)", text, re.I
    ):
        values = match.group("values")
        if re.search(r"\b(?:styles?|colou?rs?|materials?)\b", values, re.I):
            continue
        if re.search(r"\b(?:brands?|sellers?)\b", values, re.I):
            if re.search(r"\b(?:offering|good\s+quality|affordable)\b", values, re.I):
                preferences["seller_preference"] = re.sub(r"\s+", " ", values).strip(" ,.")
            else:
                brand_values = _split_specification_items(values, split_or=True)
                if brand_values:
                    preferences["brands"] = _merge_brand_values(
                        preferences.get("brands", ""),
                        ", ".join(brand_values),
                    )


def _extract_brand_preferences(text: str, preferences: dict[str, str]) -> None:
    for match in _PREFERENCE_PATTERN.finditer(text):
        values = match.group(1).strip(" ,.")
        if re.search(r"\b(?:styles?|colou?rs?|materials?)\b", values, re.I):
            continue
        if re.search(r"\b(?:brands?|sellers?)\b", values, re.I) and re.search(
            r"\b(?:offering|good\s+quality|affordable)\b", values, re.I
        ):
            continue
        brand_values: list[str] = []
        for item in _split_specification_items(values, split_or=True):
            if _looks_like_preference_attribute(item):
                key, value = _specification_key_value(item)
                if key:
                    preferences[key] = value
            else:
                brand_values.append(item)
        if brand_values:
            preferences["brands"] = _merge_brand_values(
                preferences.get("brands", ""),
                ", ".join(brand_values),
            )


def _looks_like_preference_attribute(value: str) -> bool:
    if re.match(
        r"^(?:a|an|the|good|long|extended|lightweight|light[- ]weight|"
        r"backlit|quiet|low[- ]|high[- ]|at least|at most|minimum|maximum)\b",
        value,
        re.I,
    ):
        return True
    folded = value.casefold()
    return any(re.search(rf"\b{re.escape(suffix)}\b", folded) for suffix, _ in _SPECIFICATION_SUFFIXES)


def _extract_explicit_requirements(
    messages: Sequence[ConversationMessage],
) -> RequirementDraft:
    """Recover clearly stated facts if the model omits them from its JSON draft."""
    extracted = RequirementDraft()
    previous_assistant_question = ""
    existing_product = ""

    for message in messages:
        if message.role == "assistant":
            previous_assistant_question = message.content.casefold()
            continue
        text = message.content
        is_short_unit_answer = bool(_SHORT_UNIT_PATTERN.fullmatch(text))
        asks_quantity = any(
            phrase in previous_assistant_question
            for phrase in ("how many", "quantity")
        )
        asks_unit = "unit" in previous_assistant_question
        asks_capacity = "capacity" in previous_assistant_question

        product_match = next(
            (match for pattern in _PRODUCT_PATTERNS if (match := pattern.search(text))),
            None,
        )
        if product_match:
            product_name = product_match.group(1).strip(" .,:;")
            product_name = re.sub(
                rf"^{_NUMBER_WORD_PATTERN}\s+"
                r"(?:kilograms?|kg|tonnes?|tons?|litres?|liters?)\s+of\s+",
                "",
                product_name,
                flags=re.IGNORECASE,
            )
            if _VARIETY_QUANTITY_PATTERN.search(text) and re.search(r"\bseeds?\b", text, re.I):
                product_name = "seeds"
            product_name = re.sub(
                rf"^{_NUMBER_WORD_PATTERN}\s+",
                "",
                product_name,
                flags=re.IGNORECASE,
            )
            if product_name and product_name.casefold() not in _NON_PRODUCT_TERMS:
                product_name = {
                    "laptops": "Laptop",
                    "phones": "Phone",
                    "monitors": "Monitor",
                    "tanks": "Tank",
                    "watches": "Watch",
                    "smartwatches": "Smartwatch",
                    "chairs": "Chair",
                    "tables": "Table",
                    "cameras": "Camera",
                    "printers": "Printer",
                }.get(product_name.casefold(), product_name)
                extracted.product_name = product_name
                extracted.product_type = product_name
                existing_product = product_name
                device_identity = _DELL_XPS_PATTERN.search(product_name)
                if device_identity:
                    extracted.product_type = device_identity.group("product_type").capitalize()
                    extracted.mandatory_requirements["brand"] = device_identity.group("brand")
                    extracted.mandatory_requirements["model"] = re.sub(
                        r"\s+",
                        " ",
                        device_identity.group("model"),
                    ).upper()
                category_match = _COUNTABLE_PRODUCT_PATTERN.search(product_name)
                if category_match:
                    product_key = category_match.group(1).casefold().rstrip("s")
                    category = _PRODUCT_CATEGORIES.get(product_key)
                    if category:
                        extracted.category = category

        if not existing_product:
            existing_product = extracted.product_name or ""

        _extract_specification_clauses(
            text,
            extracted.mandatory_requirements,
            extracted.optional_preferences,
        )
        display_size_match = _DISPLAY_SIZE_PATTERN.search(text)
        if display_size_match:
            extracted.mandatory_requirements["display_size"] = (
                f"{display_size_match.group('size').replace(',', '.')} inches"
            )
        display_type_match = _DISPLAY_TYPE_PATTERN.search(text)
        if display_type_match:
            extracted.mandatory_requirements["display_type"] = (
                f"{display_type_match.group('display_type').upper()}"
                f"{display_type_match.group('plus')}"
            )
        capacity_match = _CAPACITY_PATTERN.search(text)
        if capacity_match:
            amount = capacity_match.group(1) or capacity_match.group(3)
            unit = capacity_match.group(2) or capacity_match.group(4)
            extracted.mandatory_requirements["capacity"] = f"{amount} {unit}"
        elif asks_capacity:
            short_measurement = re.fullmatch(
                r"\s*(\d+(?:[.,]\d+)?)\s*"
                r"(litres?|liters?|gallons?|ml|millilitres?|milliliters?|"
                r"m3|cubic\s+met(?:er|re)s?)\s*[.!]?\s*",
                text,
                re.IGNORECASE,
            )
            if short_measurement:
                extracted.mandatory_requirements["capacity"] = (
                    f"{short_measurement.group(1)} {short_measurement.group(2)}"
                )

        material_matches = list(_MATERIAL_PATTERN.finditer(text))
        if material_matches:
            material_match = next(
                (match for match in material_matches if re.search(r"\d{3,4}", match.group(0))),
                material_matches[-1],
            )
            material = material_match.group(0).strip()
            if material.casefold().startswith("ss"):
                grade = re.search(r"\d{3,4}", material)
                material = f"SS {grade.group(0)}" if grade else "stainless steel"
            extracted.mandatory_requirements["material"] = material

        use_case_match = _USE_CASE_PREFERENCE_PATTERN.search(text)
        if use_case_match:
            extracted.optional_preferences["application"] = "Professional use"
        application_match = _APPLICATION_PATTERN.search(text)
        if application_match and not use_case_match:
            application = application_match.group(1).strip(" .,:;")
            application = re.sub(r"^my\s+", "", application, flags=re.I)
            if application:
                extracted.mandatory_requirements["application"] = _merge_application_values(
                    extracted.mandatory_requirements.get("application", ""),
                    application,
                )

        ram_match = _RAM_PATTERN.search(text)
        if ram_match:
            qualifier = (
                "Minimum "
                if re.match(r"\s*(?:at least|minimum|min\.?)", ram_match.group(0), re.I)
                else ""
            )
            extracted.mandatory_requirements["ram"] = (
                f"{qualifier}{ram_match.group(1)}{ram_match.group(2).upper()}"
            )

        storage_match = _STORAGE_PATTERN.search(text)
        if storage_match:
            qualifier = (
                "Minimum "
                if re.match(r"\s*(?:at least|minimum|min\.?)", storage_match.group(0), re.I)
                else ""
            )
            extracted.mandatory_requirements["storage"] = (
                f"{qualifier}{storage_match.group(1)}{storage_match.group(2).upper()} "
                f"{storage_match.group(3).upper()}"
            )

        voltage_match = _VOLTAGE_PATTERN.search(text)
        if voltage_match:
            extracted.mandatory_requirements["voltage"] = (
                f"{voltage_match.group(1)} {voltage_match.group(2)}"
            )

        power_match = _POWER_PATTERN.search(text)
        if power_match:
            extracted.mandatory_requirements["power"] = (
                f"{power_match.group(1)} {power_match.group(2)}"
            )
        _extract_negative_requirements(text, extracted.mandatory_requirements)
        _extract_material_characteristics(text, extracted.mandatory_requirements)
        _extract_classified_preferences(text, extracted.optional_preferences)

        _extract_brand_preferences(text, extracted.optional_preferences)
        battery_match = _BATTERY_PATTERN.search(text)
        if battery_match:
            extracted.optional_preferences["battery_life"] = battery_match.group(1).capitalize()
        weight_match = _WEIGHT_PATTERN.search(text)
        if weight_match:
            extracted.optional_preferences["weight"] = "Lightweight"
        keyboard_match = _KEYBOARD_PATTERN.search(text)
        if keyboard_match:
            extracted.optional_preferences["keyboard"] = "Backlit"

        quantity_match = None
        for match in _QUANTITY_PATTERN.finditer(text):
            unit = (match.group("unit") or "").casefold()
            following_text = text[match.end():]
            is_technical_measurement = unit in {
                "gb", "tb", "v", "volt", "volts", "w", "kw", "watt", "watts",
                "mhz", "ghz", "hz", "mah", "ah", "%",
            }
            is_capacity_measurement = unit in {
                "litre", "litres", "liter", "liters", "ml", "millilitre",
                "millilitres", "milliliter", "milliliters", "gallon", "gallons",
                "m", "meter", "meters", "metre", "metres", "cm", "mm",
            } and re.match(
                r"\s*(?:of\s+)?(?:storage\s+)?(?:capacity|volume|dimensions?)\b",
                following_text,
                re.I,
            )
            if not is_technical_measurement and not is_capacity_measurement:
                quantity_match = match
                break
        short_quantity_match = _SHORT_QUANTITY_PATTERN.fullmatch(text)
        if short_quantity_match and (asks_quantity or short_quantity_match.group(2)):
            number_text = short_quantity_match.group(1)
            quantity_value = _normalize_number(number_text)
            extracted.commercial_constraints["quantity"] = quantity_value
            answer_product_unit = short_quantity_match.group(2)
            if answer_product_unit:
                if not existing_product:
                    inferred_product = {
                        "watches": "watch",
                        "laptops": "laptop",
                        "phones": "phone",
                        "mobile phones": "mobile phone",
                        "monitors": "monitor",
                        "tanks": "tank",
                        "chairs": "chair",
                        "tables": "table",
                        "cameras": "camera",
                        "printers": "printer",
                        "hair bands": "hair band",
                    }.get(answer_product_unit.casefold(), answer_product_unit.rstrip("s"))
                    extracted.product_name = inferred_product.capitalize()
                    extracted.product_type = inferred_product.capitalize()
                    existing_product = inferred_product
                    category = _PRODUCT_CATEGORIES.get(inferred_product.casefold())
                    if category:
                        extracted.category = category
                if answer_product_unit.casefold() in {
                    "laptop", "laptops", "phone", "phones", "mobile phone",
                    "mobile phones", "monitor", "monitors", "tank", "tanks",
                    "chair", "chairs", "table", "tables", "camera", "cameras",
                    "printer", "printers", "hair band", "hair bands",
                }:
                    extracted.commercial_constraints["unit"] = "units"
                elif answer_product_unit.casefold() in {"watch", "watches"}:
                    extracted.commercial_constraints["unit"] = "watches"
                else:
                    extracted.commercial_constraints["unit"] = answer_product_unit.lower()
            elif _COUNTABLE_PRODUCT_PATTERN.search(existing_product):
                extracted.commercial_constraints["unit"] = "units"
        elif is_short_unit_answer and asks_unit:
            unit_value = _SHORT_UNIT_PATTERN.fullmatch(text).group(1).casefold()
            if unit_value in {
                "laptop", "laptops", "phone", "phones", "mobile phone", "mobile phones",
                "monitor", "monitors", "tank", "tanks", "chair", "chairs",
                "table", "tables", "camera", "cameras", "printer", "printers",
                "hair band", "hair bands",
            }:
                unit_value = "units"
            elif unit_value in {"watch", "watches"}:
                unit_value = "watches"
            extracted.commercial_constraints["unit"] = unit_value

        if quantity_match:
            quantity = _normalize_number(quantity_match.group("number"))
            extracted.commercial_constraints["quantity"] = quantity
            supplied_unit = quantity_match.group("unit")
            if supplied_unit:
                supplied_unit = supplied_unit.lower()
                if supplied_unit in {
                    "laptop", "laptops", "phone", "phones", "mobile phone",
                    "mobile phones", "monitor", "monitors", "tank", "tanks",
                    "chair", "chairs", "table", "tables", "camera", "cameras",
                    "printer", "printers", "hair band", "hair bands",
                }:
                    extracted.commercial_constraints["unit"] = "units"
                elif supplied_unit in {"watch", "watches"}:
                    extracted.commercial_constraints["unit"] = "watches"
                else:
                    extracted.commercial_constraints["unit"] = supplied_unit.rstrip(".")

        total_budget_quantity = _TOTAL_BUDGET_QUANTITY_PATTERN.search(text)
        if total_budget_quantity and re.search(
            r"\b(?:total|overall|whole\s+order|entire\s+order|within)\b",
            text,
            re.I,
        ):
            extracted.commercial_constraints["quantity"] = _normalize_number(
                total_budget_quantity.group("number")
            )
            extracted.commercial_constraints["unit"] = (
                total_budget_quantity.group("unit").casefold()
            )

        current_quantity = extracted.commercial_constraints.get("quantity")
        try:
            quantity_value = Decimal(current_quantity) if current_quantity else None
        except InvalidOperation:
            quantity_value = None
        price = understand_price(text, quantity=quantity_value)
        extracted.commercial_constraints.update(price.values)
        if price.clarification:
            extracted.commercial_constraints["price_clarification"] = price.clarification
        _extract_variety_quantities(text, extracted.commercial_constraints)

        location_match = _DELIVERY_LOCATION_PATTERN.search(text)
        if location_match:
            extracted.delivery_constraints["location"] = location_match.group(1).strip(" .,:;")

        delivery_match = _DELIVERY_REQUIREMENT_PATTERN.search(text)
        if delivery_match:
            extracted.delivery_constraints["requirement"] = delivery_match.group(1).strip(" .,:;")
        if _RECOMMENDATION_STYLE_PATTERN.search(text):
            extracted.optional_preferences["recommendation_style"] = "Similar alternatives"

        if not quantity_match and not (short_quantity_match and asks_quantity):
            number = re.match(rf"^\s*({_NUMBER_WORD_PATTERN})\s*[.!]?\s*$", text, re.I)
            if number and asks_quantity:
                extracted.commercial_constraints["quantity"] = _normalize_number(number.group(1))
                if _COUNTABLE_PRODUCT_PATTERN.search(existing_product):
                    extracted.commercial_constraints["unit"] = "units"

    return RequirementDraft.model_validate(extracted.model_dump())


def _has_purchase_quantity(draft: RequirementDraft) -> bool:
    value = draft.commercial_constraints.get("quantity")
    if value is None:
        return False
    try:
        quantity = Decimal(value)
    except InvalidOperation:
        return False
    return quantity.is_finite() and quantity > 0


def _normalize_purchase_quantity(draft: RequirementDraft) -> RequirementDraft:
    commercial = dict(draft.commercial_constraints)
    value = commercial.get("quantity")
    if value:
        match = re.fullmatch(
            rf"\s*({_NUMBER_WORD_PATTERN})\s*"
            r"(units?|pieces?|pcs|items?|sets?|packs?|boxes?|hair\s+bands?|laptops?|phones?|"
            r"mobile\s+phones?|monitors?|tanks?|watches?|kilograms?|kg|litres?|liters?)?\s*",
            value,
            re.IGNORECASE,
        )
        if match:
            commercial["quantity"] = _normalize_number(match.group(1))
            if match.group(2):
                supplied_unit = match.group(2).casefold()
                if supplied_unit in {
                    "laptop", "laptops", "phone", "phones", "monitor",
                    "mobile phone", "mobile phones", "monitors", "tank", "tanks",
                    "chair", "chairs", "table", "tables", "camera", "cameras",
                    "printer", "printers", "hair band", "hair bands",
                }:
                    commercial["unit"] = "units"
                elif supplied_unit in {"watch", "watches"}:
                    commercial["unit"] = "watches"
                else:
                    commercial["unit"] = supplied_unit
    existing_unit = commercial.get("unit", "").casefold()
    if existing_unit in {
        "laptop", "laptops", "phone", "phones", "monitor",
        "mobile phone", "mobile phones", "monitors", "tank", "tanks",
        "chair", "chairs", "table", "tables", "camera", "cameras",
        "printer", "printers", "hair band", "hair bands",
    }:
        commercial["unit"] = "units"
    elif existing_unit in {"watch", "watches"}:
        commercial["unit"] = "watches"
    if commercial.get("quantity") and not commercial.get("unit"):
        product = f"{draft.product_name or ''} {draft.product_type or ''}"
        if _COUNTABLE_PRODUCT_PATTERN.search(product):
            commercial["unit"] = "units"
    return RequirementDraft.model_validate(
        {**draft.model_dump(), "commercial_constraints": commercial}
    )


def _missing_essentials(draft: RequirementDraft) -> list[str]:
    missing: list[str] = []
    if not (draft.product_name or draft.product_type):
        missing.append("product_name")
    elif (
        "tank" in (draft.product_name + " " + (draft.product_type or "")).casefold()
        and not draft.mandatory_requirements.get("capacity")
    ):
        missing.append("capacity")
    if not _has_purchase_quantity(draft):
        missing.append("quantity")
    if not draft.commercial_constraints.get("unit"):
        missing.append("unit")
    commercial = draft.commercial_constraints
    if commercial.get("price_clarification"):
        missing.append("price_clarification")
    else:
        price_fields = (
            "min_target_unit_price",
            "minimum_target_unit_price",
            "max_target_unit_price",
            "maximum_target_unit_price",
            "target_unit_price",
            "total_budget",
        )
        supplied_prices = [
            commercial[field]
            for field in price_fields
            if commercial.get(field) is not None
        ]
        valid_prices = True
        for value in supplied_prices:
            try:
                price = Decimal(value)
            except (InvalidOperation, ValueError):
                valid_prices = False
                break
            if not price.is_finite() or price <= 0:
                valid_prices = False
                break
        if not supplied_prices:
            missing.append("price")
        elif not valid_prices:
            missing.append("price_clarification")
        else:
            minimum = commercial.get(
                "min_target_unit_price",
                commercial.get("minimum_target_unit_price"),
            )
            maximum = commercial.get(
                "max_target_unit_price",
                commercial.get("maximum_target_unit_price"),
            )
            if minimum and maximum:
                if Decimal(maximum) < Decimal(minimum):
                    missing.append("price_clarification")
            elif minimum and commercial.get("price_type") == "range":
                missing.append("price_clarification")
            if commercial.get("price_type") == "ambiguous_budget":
                missing.append("price_clarification")
        if supplied_prices:
            currency = commercial.get("currency", "").upper()
            if not currency:
                missing.append("currency")
            elif currency not in SUPPORTED_CURRENCY_CODES:
                missing.append("price_clarification")
    return missing


def _resolve_ambiguous_budget_scope(
    draft: RequirementDraft,
    answer: str,
    previous_assistant: Optional[ConversationMessage],
) -> RequirementDraft:
    commercial = dict(draft.commercial_constraints)
    if (
        commercial.get("price_type") != "ambiguous_budget"
        or not commercial.get("budget")
        or previous_assistant is None
        or "each unit or for the whole order" not in previous_assistant.content.casefold()
    ):
        return draft

    answer_lower = answer.casefold()
    if re.search(r"\b(?:per|for each|each)\s+(?:unit|piece|item)\b", answer_lower):
        amount = commercial.pop("budget")
        commercial["max_target_unit_price"] = amount
        commercial["price_type"] = "maximum"
    elif re.search(
        r"\b(?:whole order|entire order|total|altogether|in all|overall)\b",
        answer_lower,
    ):
        amount = commercial.pop("budget")
        commercial["total_budget"] = amount
        commercial["price_type"] = "total_budget"
        quantity = commercial.get("quantity")
        try:
            quantity_value = Decimal(quantity) if quantity else None
            budget_value = Decimal(amount)
        except InvalidOperation:
            quantity_value = None
            budget_value = None
        if (
            quantity_value is not None
            and quantity_value.is_finite()
            and quantity_value > 0
            and budget_value is not None
            and budget_value.is_finite()
            and budget_value > 0
        ):
            commercial["derived_max_unit_budget"] = str(
                budget_value / quantity_value
            )
            commercial["derived_unit_budget_source"] = (
                "total_budget_divided_by_quantity"
            )
            commercial["derived_max_unit_budget_is_derived"] = "true"
    else:
        return draft

    commercial.pop("price_clarification", None)
    commercial["is_derived"] = "false"
    return RequirementDraft.model_validate(
        {**draft.model_dump(), "commercial_constraints": commercial}
    )


def _merge_draft(
    previous: Optional[RequirementDraft],
    current: RequirementDraft,
    *,
    overwrite: bool = True,
    merge_applications: bool = True,
    merge_distinct_values: bool = True,
) -> RequirementDraft:
    if previous is None:
        return current

    merged = previous.model_dump()
    current_values = current.model_dump()
    for field in ("product_name", "product_type", "category"):
        if current_values[field] and (overwrite or not merged[field]):
            merged[field] = current_values[field]
    for field in (
        "mandatory_requirements",
        "optional_preferences",
        "commercial_constraints",
        "delivery_constraints",
    ):
        for key, value in current_values[field].items():
            if not value.strip():
                continue
            existing_value = merged[field].get(key, "")
            is_multi_value = key in {
                "application",
                "brands",
                "styles",
                "colours",
                "colors",
                "materials",
                "exclusions",
                "seller_preference",
                "variety_quantities",
            }
            if (
                key == "application"
                and field == "mandatory_requirements"
                and existing_value
                and merge_applications
            ):
                merged[field][key] = _merge_application_values(existing_value, value)
            elif existing_value and is_multi_value and merge_distinct_values:
                merged[field][key] = (
                    _merge_brand_values(existing_value, value)
                    if key == "brands"
                    else _merge_list_values(existing_value, value)
                )
            elif overwrite or not existing_value:
                merged[field][key] = value
    for key in current_values["mandatory_requirements"]:
        merged["optional_preferences"].pop(key, None)
    for key in current_values["optional_preferences"]:
        merged["mandatory_requirements"].pop(key, None)
    return RequirementDraft.model_validate(merged)


def _remove_battery_capacity_split_fragments(draft: RequirementDraft) -> RequirementDraft:
    values = draft.model_dump()
    for field in ("mandatory_requirements", "optional_preferences"):
        requirements = values[field]
        if not requirements.get("battery_capacity"):
            continue
        for key, value in list(requirements.items()):
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
            normalized_value = value.casefold().strip()
            partial_capacity = normalized_key == "capacity" and re.fullmatch(
                r"battery\s+of\s+(?:at\s+least\s+)?\d+(?:\.\d+)?",
                normalized_value,
            )
            split_qualifier = normalized_key.endswith("or_higher") and re.fullmatch(
                r"\d+(?:,\d{3})*\s*mAh", value, re.IGNORECASE
            )
            split_unit_key = re.fullmatch(r"\d+(?:,\d{3})*mAh", key, re.IGNORECASE) and re.fullmatch(
                r"\d+(?:,\d{3})*\s*mAh", value, re.IGNORECASE
            )
            if partial_capacity or split_qualifier or split_unit_key:
                del requirements[key]
    return RequirementDraft.model_validate(values)


def _normalize_recommendation_preferences(draft: RequirementDraft) -> RequirementDraft:
    values = draft.model_dump()
    delivery = values["delivery_constraints"]
    preferences = values["optional_preferences"]
    for key, value in list(delivery.items()):
        if _RECOMMENDATION_STYLE_PATTERN.search(f"{key} {value}") or (
            "recommend" in key.casefold()
            or "alternative" in key.casefold()
            or "similar" in key.casefold()
        ):
            preferences.setdefault("recommendation_style", "Similar alternatives")
            del delivery[key]
    application = preferences.get("application")
    if application and re.search(r"\bprofessional\s+use\b", application, re.IGNORECASE):
        preferences["application"] = "Professional use"
    return RequirementDraft.model_validate(values)


class ConversationService:
    """Applies backend completeness rules to provider-generated requirement drafts."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def respond(self, request: AIConversationRequest) -> AIConversationResponse:
        draft = request.requirement_draft or RequirementDraft()
        settings = get_settings()
        input_characters = (
            len(request.message)
            + sum(len(item.content) for item in request.conversation_history)
            + len(json.dumps(draft.model_dump(), ensure_ascii=False))
        )
        logging.getLogger("markettwin.ai.conversation").info(
            "AI conversation service started request_id=%s provider=%s model=%s input_characters=%d",
            request_id_context.get(),
            settings.AI_PROVIDER,
            (
                settings.OLLAMA_MODEL
                if settings.AI_PROVIDER.strip().lower() == "ollama"
                else settings.GROQ_MODEL
                if settings.AI_PROVIDER.strip().lower() == "groq"
                else "unknown"
            ),
            input_characters,
        )
        generated = await self._provider.converse(
            request.message,
            request.conversation_history,
            draft,
        )
        current_message = ConversationMessage(role="user", content=request.message)
        historical_extraction = _extract_explicit_requirements(request.conversation_history)
        previous_assistant = next(
            (
                item
                for item in reversed(request.conversation_history)
                if item.role == "assistant"
            ),
            None,
        )
        current_context = (
            [previous_assistant, current_message]
            if previous_assistant
            else [current_message]
        )
        current_extraction = _extract_explicit_requirements(current_context)
        correction_requested = bool(
            re.search(
                r"\b(?:actually|instead|correction|change|replace|make\s+that|"
                r"rather\s+than)\b",
                request.message,
                re.IGNORECASE,
            )
        )
        merged_draft = _merge_draft(
            _merge_draft(
                _merge_draft(draft, historical_extraction, overwrite=False),
                generated.requirement_draft,
                overwrite=correction_requested,
                merge_distinct_values=not correction_requested,
            ),
            current_extraction,
            overwrite=True,
            merge_applications=not correction_requested,
            merge_distinct_values=not correction_requested,
        )
        merged_draft = _remove_battery_capacity_split_fragments(merged_draft)
        merged_values = merged_draft.model_dump()
        merged_commercial = merged_values["commercial_constraints"]
        if any(
            key in current_extraction.commercial_constraints
            for key in (
                "min_target_unit_price",
                "max_target_unit_price",
                "target_unit_price",
                "total_budget",
            )
        ):
            merged_commercial.pop("price_clarification", None)
        if (
            "minimum_target_unit_price" in merged_commercial
            and "min_target_unit_price" not in merged_commercial
        ):
            merged_commercial["min_target_unit_price"] = merged_commercial[
                "minimum_target_unit_price"
            ]
        merged_draft = RequirementDraft.model_validate(merged_values)
        merged_draft = _normalize_purchase_quantity(merged_draft)
        merged_draft = _resolve_ambiguous_budget_scope(
            merged_draft,
            request.message,
            previous_assistant,
        )
        merged_draft = _normalize_recommendation_preferences(merged_draft)
        commercial = dict(merged_draft.commercial_constraints)
        minimum = commercial.get(
            "min_target_unit_price",
            commercial.get("minimum_target_unit_price"),
        )
        maximum = commercial.get(
            "max_target_unit_price",
            commercial.get("maximum_target_unit_price"),
        )
        if minimum and maximum:
            try:
                contradictory_bounds = Decimal(minimum) > Decimal(maximum)
            except InvalidOperation:
                contradictory_bounds = False
            if contradictory_bounds:
                commercial["price_clarification"] = (
                    "Please clarify the price range; its minimum is above its maximum."
                )
                merged_draft = RequirementDraft.model_validate(
                    {**merged_draft.model_dump(), "commercial_constraints": commercial}
                )

        missing = _missing_essentials(merged_draft)
        questions = [_ESSENTIAL_QUESTIONS[missing[0]]] if missing else []
        if questions:
            custom_clarification = merged_draft.commercial_constraints.get(
                "price_clarification"
            )
            assistant_message = (
                custom_clarification
                if missing[0] == "price_clarification" and custom_clarification
                else "To continue, please tell me: " + " ".join(questions)
            )
        elif missing:
            assistant_message = "I still need the required details requested above before completing the summary."
        else:
            assistant_message = (
                "I have enough required information. Please review the structured "
                "requirement summary."
            )

        return AIConversationResponse(
            assistant_message=assistant_message,
            status="ready" if not missing else "collecting_information",
            requirement_draft=merged_draft,
            missing_essential_information=missing,
            clarification_questions=questions,
            ready_for_confirmation=not missing,
        )
