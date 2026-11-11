import json
import re
from typing import Any, Literal, Optional
from decimal import Decimal, InvalidOperation

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


_PROVIDER_PRICE_FIELDS = (
    "min_target_unit_price",
    "max_target_unit_price",
    "target_unit_price",
    "minimum_target_unit_price",
    "maximum_target_unit_price",
    "total_budget",
    "budget",
    "derived_max_unit_budget",
)
_COMMERCIAL_REQUIREMENT_KEYS = {
    "price_type": "price_type",
    "maximum_unit_price": "max_target_unit_price",
    "max_unit_price": "max_target_unit_price",
    "max_budget_unit_price": "max_target_unit_price",
    "maximum_budget_unit_price": "max_target_unit_price",
    "minimum_unit_price": "min_target_unit_price",
    "min_unit_price": "min_target_unit_price",
    "minimum_budget_unit_price": "min_target_unit_price",
    "min_budget_unit_price": "min_target_unit_price",
    "budget": "budget",
    "maximum_budget": "max_target_unit_price",
    "minimum_budget": "min_target_unit_price",
    "target_unit_price": "target_unit_price",
    "total_budget": "total_budget",
    "quantity": "quantity",
    "unit": "unit",
    "currency": "currency",
}
_PRICE_AMOUNT_PATTERN = re.compile(
    r"^\s*(?:(?P<prefix>maximum(?:\s+budget)?|minimum(?:\s+budget)?|"
    r"max(?:imum)?|min(?:imum)?|up\s+to|at\s+most|at\s+least|"
    r"not\s+more\s+than|not\s+less\s+than|budget|price|target\s+price)\s*[:=]?\s*)?"
    r"(?:(?P<symbol>₹|Rs\.?|INR|USD|EUR|GBP|CAD|AUD|JPY|CNY|CHF|SGD|NZD|"
    r"\$|€|£|¥)\s*)?"
    r"(?:\{\s*)?(?P<number>(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<scale>k|thousand|lakh|lakhs|lac|lacs|million|mn|crore|crores)?"
    r"\s*\}?\s*(?P<suffix>INR|USD|EUR|GBP|CAD|AUD|JPY|CNY|CHF|SGD|NZD|"
    r"rupees?|dollars?|euros?|pounds?)?\s*(?:only)?\s*$",
    re.IGNORECASE,
)
_PRICE_SCALES = {
    "k": Decimal(1000),
    "thousand": Decimal(1000),
    "lakh": Decimal(100000),
    "lakhs": Decimal(100000),
    "lac": Decimal(100000),
    "lacs": Decimal(100000),
    "million": Decimal(1000000),
    "mn": Decimal(1000000),
    "crore": Decimal(10000000),
    "crores": Decimal(10000000),
}
_CURRENCY_CODES = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}


def _normalize_price_amount(value: Any) -> tuple[Any, Optional[str]]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            return value, None
        if not amount.is_finite():
            return value, None
        return format(amount.normalize(), "f"), None
    if not isinstance(value, str):
        return value, None

    match = _PRICE_AMOUNT_PATTERN.fullmatch(value)
    if match is None:
        return value, None
    try:
        amount = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        return value, None
    scale = match.group("scale")
    if scale:
        amount *= _PRICE_SCALES[scale.casefold()]
    if not amount.is_finite():
        return value, None
    currency_token = match.group("suffix") or match.group("symbol")
    currency = _CURRENCY_CODES.get(currency_token.casefold()) if currency_token else None
    if currency is None and currency_token:
        token = currency_token.casefold()
        if re.fullmatch(r"[a-z]{3}", token):
            currency = token.upper()
    return format(amount.normalize(), "f"), currency


def normalize_commercial_requirement_fields(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    commercial_value = normalized.get("commercial_constraints")
    commercial = dict(commercial_value) if isinstance(commercial_value, dict) else {}

    for requirement_field in ("mandatory_requirements", "optional_preferences"):
        requirements_value = normalized.get(requirement_field)
        if not isinstance(requirements_value, dict):
            continue
        requirements = dict(requirements_value)
        for key, raw_value in list(requirements.items()):
            normalized_key = re.sub(
                r"[^a-z0-9]+", "_", str(key).casefold()
            ).strip("_")
            if normalized_key != "price_type":
                continue
            commercial.setdefault("price_type", str(raw_value).strip().casefold())
            requirements.pop(key)
        normalized[requirement_field] = requirements

    for requirement_field in ("mandatory_requirements", "optional_preferences"):
        requirements_value = normalized.get(requirement_field)
        if not isinstance(requirements_value, dict):
            continue
        requirements = dict(requirements_value)
        for key, raw_value in list(requirements.items()):
            normalized_key = re.sub(
                r"[^a-z0-9]+", "_", str(key).casefold()
            ).strip("_")
            commercial_key = _COMMERCIAL_REQUIREMENT_KEYS.get(normalized_key)
            if commercial_key is None:
                continue
            if commercial_key == "budget":
                price_type = str(commercial.get("price_type", "")).casefold()
                if price_type == "maximum":
                    commercial_key = "max_target_unit_price"
                elif price_type == "minimum":
                    commercial_key = "min_target_unit_price"

            if commercial_key in {
                "max_target_unit_price",
                "min_target_unit_price",
                "maximum_target_unit_price",
                "minimum_target_unit_price",
                "target_unit_price",
                "total_budget",
                "budget",
                "maximum_budget",
                "minimum_budget",
            }:
                amount_value = raw_value
                if isinstance(raw_value, dict):
                    amount_value = raw_value.get("value")
                    currency = raw_value.get("currency")
                    if (
                        isinstance(currency, str)
                        and currency.strip()
                        and not commercial.get("currency")
                    ):
                        commercial["currency"] = currency.strip().upper()
                    if amount_value is None:
                        amount_value = json.dumps(raw_value, sort_keys=True)
                amount, parsed_currency = _normalize_price_amount(amount_value)
                commercial.setdefault(commercial_key, amount)
                if parsed_currency and not commercial.get("currency"):
                    commercial["currency"] = parsed_currency
            else:
                commercial.setdefault(commercial_key, str(raw_value).strip())
            requirements.pop(key)
        normalized[requirement_field] = requirements

    normalized["commercial_constraints"] = commercial

    for canonical, aliases in (
        (
            "max_target_unit_price",
            (
                "maximum_target_unit_price",
                "maximum_budget",
                "maximum_unit_price",
                "max_budget_unit_price",
                "maximum_budget_unit_price",
            ),
        ),
        (
            "min_target_unit_price",
            (
                "minimum_target_unit_price",
                "minimum_budget",
                "minimum_unit_price",
                "min_budget_unit_price",
                "minimum_budget_unit_price",
            ),
        ),
    ):
        present = [
            (key, commercial[key])
            for key in (canonical, *aliases)
            if commercial.get(key) is not None
        ]
        if not present:
            for alias in aliases:
                commercial.pop(alias, None)
            continue

        normalized_amounts = [
            _normalize_price_amount(amount) for _, amount in present
        ]
        values = {str(amount) for amount, _ in normalized_amounts}
        currencies = {
            currency for _, currency in normalized_amounts if currency is not None
        }
        if len(values) > 1 or len(currencies) > 1:
            commercial.setdefault(
                "price_clarification",
                "Conflicting maximum or minimum unit price values were provided.",
            )
        selected_amount, selected_currency = normalized_amounts[0]
        commercial[canonical] = selected_amount
        if selected_currency and not commercial.get("currency"):
            commercial["currency"] = selected_currency
        for alias in aliases:
            commercial.pop(alias, None)

    if str(commercial.get("price_type", "")).casefold() == "maximum":
        legacy_budget = commercial.pop("budget", None)
        if legacy_budget is not None:
            amount, currency = _normalize_price_amount(legacy_budget)
            existing = commercial.get("max_target_unit_price")
            if existing is not None and str(existing) != str(amount):
                commercial.setdefault(
                    "price_clarification",
                    "Conflicting maximum unit price values were provided.",
                )
            else:
                commercial["max_target_unit_price"] = amount
            if currency and not commercial.get("currency"):
                commercial["currency"] = currency
    elif str(commercial.get("price_type", "")).casefold() == "minimum":
        legacy_budget = commercial.pop("budget", None)
        if legacy_budget is not None:
            amount, currency = _normalize_price_amount(legacy_budget)
            existing = commercial.get("min_target_unit_price")
            if existing is not None and str(existing) != str(amount):
                commercial.setdefault(
                    "price_clarification",
                    "Conflicting minimum unit price values were provided.",
                )
            else:
                commercial["min_target_unit_price"] = amount
            if currency and not commercial.get("currency"):
                commercial["currency"] = currency

    normalized["commercial_constraints"] = commercial
    return normalized


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message content cannot be blank.")
        return value


class RequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: Optional[str] = Field(
        default=None,
        max_length=160,
        description="Product name stated by the customer.",
    )
    product_type: Optional[str] = Field(
        default=None,
        max_length=160,
        description="Product type stated or clearly identifiable from customer messages.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Product category only when identifiable from customer messages.",
    )
    mandatory_requirements: dict[str, str] = Field(
        default_factory=dict,
        max_length=32,
        description=(
            "Explicitly required product attributes. Use keys such as material, capacity, "
            "and application; capacity is not purchase quantity."
        ),
    )
    optional_preferences: dict[str, str] = Field(
        default_factory=dict,
        max_length=32,
        description="Customer-stated optional likes and nice-to-have preferences only.",
    )
    commercial_constraints: dict[str, str] = Field(
        default_factory=dict,
        max_length=16,
        description=(
            "Commercial facts. Store purchase quantity as a numeric string under quantity, "
            "its unit separately under unit, and any stated budget under budget."
        ),
    )
    delivery_constraints: dict[str, str] = Field(
        default_factory=dict,
        max_length=16,
        description=(
            "Delivery facts stated by the customer, using location for destination and "
            "requirement for delivery timing or instructions."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_constraint_maps(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        map_fields = (
            "mandatory_requirements",
            "optional_preferences",
            "commercial_constraints",
            "delivery_constraints",
        )
        for field in map_fields:
            constraints = value.get(field)
            if constraints is None:
                normalized[field] = {}
                continue
            if not isinstance(constraints, dict):
                continue

            cleaned = {}
            for key, item in constraints.items():
                if item is None or isinstance(item, str) and not item.strip():
                    continue
                cleaned[key] = item
            normalized[field] = cleaned

        normalized = normalize_commercial_requirement_fields(normalized)
        commercial = normalized.get("commercial_constraints")
        if isinstance(commercial, dict):
            commercial = dict(commercial)
            explicit_currencies = set()
            for field in _PROVIDER_PRICE_FIELDS:
                if field not in commercial:
                    continue
                amount, currency = _normalize_price_amount(commercial[field])
                commercial[field] = amount
                if currency:
                    explicit_currencies.add(currency)
            if (
                len(explicit_currencies) == 1
                and not commercial.get("currency")
            ):
                commercial["currency"] = next(iter(explicit_currencies))
            normalized["commercial_constraints"] = commercial
        return normalized

    @model_validator(mode="before")
    @classmethod
    def move_purchase_quantity_to_commercial_constraints(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        mandatory = value.get("mandatory_requirements")
        if not isinstance(mandatory, dict):
            return value

        quantity_key = re.compile(
            r"^(?:(?:purchase|order|requested)_)?quantity(?:_(\d+(?:_\d+)?))?$"
        )
        quantity_entries = []
        for key, item in mandatory.items():
            normalized_key = re.sub(
                r"[^a-z0-9]+", "_", str(key).casefold()
            ).strip("_")
            match = quantity_key.fullmatch(normalized_key)
            if match is None:
                continue
            encoded_quantity = match.group(1)
            if encoded_quantity:
                quantity = encoded_quantity.replace("_", ".")
                quantity_entries.append((key, quantity, None))
                continue
            quantity_value = str(item).strip()
            value_match = re.fullmatch(
                r"(\d+(?:[.,]\d+)?)\s*(units?|pieces?|items?|pcs?|laptops?|"
                r"phones?|sets?|packs?|boxes?)?",
                quantity_value,
                re.IGNORECASE,
            )
            if value_match:
                unit = value_match.group(2)
                normalized_unit = (
                    "units"
                    if unit and unit.casefold() in {"laptop", "laptops", "phone", "phones"}
                    else unit.casefold() if unit else None
                )
                quantity_entries.append(
                    (key, value_match.group(1).replace(",", "."), normalized_unit)
                )
        if not quantity_entries:
            return value

        normalized = dict(value)
        normalized_mandatory = dict(mandatory)
        for key, _, _ in quantity_entries:
            normalized_mandatory.pop(key)
        normalized["mandatory_requirements"] = normalized_mandatory

        commercial = value.get("commercial_constraints")
        normalized_commercial = dict(commercial) if isinstance(commercial, dict) else {}
        existing_quantity = normalized_commercial.get("quantity")
        quantities = {quantity for _, quantity, _ in quantity_entries}
        if existing_quantity is None or not str(existing_quantity).strip():
            if len(quantities) > 1:
                raise ValueError("Purchase quantity must have one unambiguous value.")
            normalized_commercial["quantity"] = quantities.pop()
        if not normalized_commercial.get("unit"):
            units = {unit for _, _, unit in quantity_entries if unit}
            if len(units) == 1:
                normalized_commercial["unit"] = units.pop()
        normalized["commercial_constraints"] = normalized_commercial
        return normalized

    @field_validator("product_name", "product_type", "category")
    @classmethod
    def optional_text_must_not_be_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator(
        "mandatory_requirements",
        "optional_preferences",
        "commercial_constraints",
        "delivery_constraints",
    )
    @classmethod
    def validate_constraint_values(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if not key.strip() or len(key) > 80:
                raise ValueError("Requirement names must contain 1 to 80 characters.")
            if not value.strip() or len(value) > 500:
                raise ValueError("Requirement values must contain 1 to 500 characters.")
        return {key.strip(): value.strip() for key, value in values.items()}

    @model_validator(mode="after")
    def prevent_mandatory_preference_overlap(self) -> "RequirementDraft":
        overlap = self.mandatory_requirements.keys() & self.optional_preferences.keys()
        if overlap:
            raise ValueError("A requirement cannot be both mandatory and an optional preference.")
        return self


class AIConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    conversation_history: list[ConversationMessage] = Field(
        default_factory=list,
        max_length=12,
        validation_alias=AliasChoices("conversation_history", "history"),
        serialization_alias="conversation_history",
    )
    requirement_draft: Optional[RequirementDraft] = None

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be blank.")
        return value

    @model_validator(mode="after")
    def limit_conversation_text(self) -> "AIConversationRequest":
        total_characters = len(self.message) + sum(len(item.content) for item in self.conversation_history)
        if total_characters > 16000:
            raise ValueError("Conversation text exceeds the allowed limit.")
        return self


class AIProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: str = Field(min_length=1, max_length=2000)
    requirement_draft: RequirementDraft

    @field_validator("assistant_message")
    @classmethod
    def assistant_message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value or value.casefold() in {"null", "none", "undefined"}:
            raise ValueError("Assistant message must contain meaningful text.")
        return value

    @model_validator(mode="after")
    def validate_generated_price_values(self) -> "AIProviderResponse":
        commercial = self.requirement_draft.commercial_constraints
        for field in _PROVIDER_PRICE_FIELDS:
            value = commercial.get(field)
            if value is None:
                continue
            try:
                amount = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{field} must be a positive numeric string.") from exc
            if not amount.is_finite() or amount <= 0:
                raise ValueError(f"{field} must be a positive numeric string.")
        return self


class AIConversationResponse(BaseModel):
    assistant_message: str
    status: Literal["collecting_information", "ready"]
    requirement_draft: RequirementDraft
    missing_essential_information: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    ready_for_confirmation: bool
