import re
import unicodedata
from typing import NamedTuple, Optional


class CanonicalDemandFamily(NamedTuple):
    product_family_key: str
    category_key: str


def _alias_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


_APPROVED_ALIASES = {
    ("hp laptop", "computers"): CanonicalDemandFamily("laptop", "computer_laptops"),
    ("reliable laptop", "computers"): CanonicalDemandFamily("laptop", "computer_laptops"),
    ("laptop suitable", "computers"): CanonicalDemandFamily("laptop", "computer_laptops"),
    ("samsung 5g smartphone", "mobile phones"): CanonicalDemandFamily("samsung_smartphone", "samsung_mobile_phones"),
    (
        "samsung galaxy smartphone suitable",
        "electronics mobile phones samsung smartphones",
    ): CanonicalDemandFamily("samsung_smartphone", "samsung_mobile_phones"),
    ("wireless over ear headphones", "electronics"): CanonicalDemandFamily("wireless_headphones", "wireless_headphones"),
    (
        "wireless over ear headphones",
        "electronics audio wireless headphones",
    ): CanonicalDemandFamily("wireless_headphones", "wireless_headphones"),
    (
        "affordable wireless over ear headphones",
        "electronics audio wireless headphones",
    ): CanonicalDemandFamily("wireless_headphones", "wireless_headphones"),
}


def canonical_demand_family(product_name: str, category: str) -> Optional[CanonicalDemandFamily]:
    """Resolve only explicitly approved product-name/category-path aliases."""
    return _APPROVED_ALIASES.get((_alias_key(product_name), _alias_key(category)))