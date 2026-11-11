import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from app.core.config import Settings, get_settings
from app.schemas.ai import RequirementDraft, normalize_commercial_requirement_fields
from app.schemas.product_matching import (
    ConstraintEvaluation,
    ConstraintState,
    MatchedProduct,
    PreferenceEvaluation,
    ProductMatchingResponse,
)
from app.services.ai.base import (
    AIProviderConfigurationError,
    AIProviderInvalidResponse,
    AIProviderTimeout,
    AIProviderUnavailable,
)
from app.services.ai.ollama_provider import OllamaProvider
from app.services.buyora_catalogue_service import (
    BuyoraCatalogueService,
    CatalogueProduct,
)
from app.services.product_matching_config import (
    BAYESIAN_GLOBAL_AVERAGE_RATING,
    BAYESIAN_MINIMUM_REVIEW_CONFIDENCE,
    MAX_AI_EXPLANATION_PRODUCTS,
    MAX_NEAR_MATCHES,
    RANKING_WEIGHTS,
)

logger = logging.getLogger("markettwin.product_matching")

_NUMBER = r"(-?\d+(?:[.,]\d+)?)"
_UNIT_ALIASES = {
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
    "millilitre": "ml",
    "millilitres": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "days": "day",
    "hours": "hour",
    "hrs": "hour",
    "minutes": "minute",
    "mins": "minute",
    "weeks": "week",
    "terabytes": "tb",
    "terabyte": "tb",
    "gigabytes": "gb",
    "gigabyte": "gb",
    "megabytes": "mb",
    "megabyte": "mb",
    "milliamps": "ma",
    "milliamp": "ma",
    "milliamphours": "mah",
}
_UNIT_FACTORS: dict[str, tuple[str, float]] = {
    "tb": ("gb", 1024),
    "gb": ("gb", 1),
    "mb": ("gb", 1 / 1024),
    "kg": ("kg", 1),
    "g": ("kg", 0.001),
    "l": ("l", 1),
    "ml": ("l", 0.001),
    "m": ("m", 1),
    "cm": ("m", 0.01),
    "mm": ("m", 0.001),
    "day": ("day", 1),
    "week": ("day", 7),
    "hour": ("day", 1 / 24),
    "minute": ("day", 1 / 1440),
    "v": ("v", 1),
    "w": ("w", 1),
    "ah": ("ah", 1),
    "mah": ("ah", 0.001),
    "hz": ("hz", 1),
    "inch": ("inch", 1),
    "inches": ("inch", 1),
    "year": ("year", 1),
    "month": ("month", 1),
    "unit": ("unit", 1),
    "units": ("unit", 1),
    "piece": ("unit", 1),
    "pieces": ("unit", 1),
}
_ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "ram": ("ram", "memory", "system memory"),
    "memory": ("memory", "ram", "system memory"),
    "storage": ("storage", "ssd", "capacity", "solid state drive"),
    "storage_capacity": ("storage", "ssd", "capacity", "solid state drive"),
    "processor": ("processor", "chip", "cpu", "chipset", "processor model"),
    "chip": ("chip", "processor", "cpu", "chipset", "processor model"),
    "display": ("display", "screen"),
    "display_size": ("display_size", "display size", "screen size", "diagonal", "screen diagonal"),
    "display_type": ("display_type", "display type", "display resolution", "screen resolution", "resolution"),
    "resolution": ("resolution", "display resolution", "screen resolution"),
    "currency": ("currency", "currency code"),
    "model": ("model", "product model"),
    "battery_life": ("battery life", "battery backup", "battery"),
    "battery": ("battery", "battery life"),
    "capacity": ("capacity", "volume"),
    "brand": ("brand", "manufacturer"),
    "colour": ("colour", "color"),
    "color": ("color", "colour"),
    "material": ("material", "construction"),
    "weight": ("weight",),
    "voltage": ("voltage",),
    "delivery_days": ("delivery", "delivery days"),
}
_GENERIC_WORDS = {
    "a", "an", "and", "for", "i", "in", "need", "of", "or", "product",
    "the", "to", "want", "with",
}
_PRODUCT_IDENTITIES: tuple[
    tuple[str, tuple[str, ...], str, tuple[str, ...]], ...
] = (
    ("scarf", ("scarves", "scarf", "neck scarves", "neck scarf"), "clothing", ("shawl",)),
    ("shawl", ("shawls", "shawl"), "clothing", ("scarf",)),
    (
        "earbuds",
        ("wireless earbuds", "earbuds", "earbud", "wireless earphones", "earphones", "earphone"),
        "electronics",
        ("headphones",),
    ),
    (
        "headphones",
        ("wireless headphones", "headphones", "headphone", "headset"),
        "electronics",
        ("earbuds",),
    ),
    (
        "laptop",
        ("notebook computer", "notebook", "laptops", "laptop"),
        "electronics",
        (),
    ),
    (
        "cookware",
        ("cast iron cookware", "cookware set", "cookware", "cooking pot", "cooking pots"),
        "kitchen",
        (),
    ),
    (
        "marine distress flag",
        ("marine distress flags", "marine distress flag", "distress flags", "distress flag"),
        "outdoors",
        (),
    ),
)
_CATEGORY_FAMILIES: dict[str, str] = {
    alias: family
    for family, aliases in {
        "clothing": (
            "clothing",
            "clothing accessories",
            "clothing and accessories",
            "apparel",
            "fashion",
            "fashion accessories",
            "garments",
            "accessories",
            "scarves",
            "shawls",
        ),
        "electronics": (
            "electronics",
            "electronic",
            "consumer electronics",
            "computers",
            "computer",
            "computing",
            "laptops",
            "laptop",
            "audio",
            "headphones",
            "earbuds",
            "smartphones",
            "mobile phones",
        ),
        "kitchen": (
            "kitchen",
            "kitchenware",
            "kitchen and dining",
            "kitchen dining",
            "cookware",
            "home and kitchen",
        ),
        "outdoors": (
            "sports and outdoors",
            "sports outdoors",
            "outdoors",
            "marine",
            "marine equipment",
            "boating",
        ),
    }.items()
    for alias in aliases
}


@dataclass(frozen=True)
class NumericValue:
    value: float
    unit: Optional[str]


@dataclass(frozen=True)
class NumericRequirement:
    lower: Optional[NumericValue] = None
    upper: Optional[NumericValue] = None
    exact: Optional[NumericValue] = None


@dataclass
class _Candidate:
    record: CatalogueProduct
    relevance: float
    relevance_eligible: bool
    identity_relation: str
    mandatory_results: list[ConstraintEvaluation]
    preference_results: list[PreferenceEvaluation]
    missing_attributes: list[str]
    rating_confidence: Optional[float]
    bayesian_rating: Optional[float]
    score: float = 0.0
    breakdown: Optional[dict[str, float]] = None
    effective_price: Optional[float] = None
    explanation: str = ""

    @property
    def failed(self) -> bool:
        return any(
            result.state == "FAIL"
            and not (
                self.identity_relation == "same"
                and result.source == "mandatory_requirements"
                and _normalize_key(result.attribute) in {"color", "colour"}
            )
            for result in self.mandatory_results
        )

    @property
    def unknown(self) -> bool:
        return any(result.state == "UNKNOWN" for result in self.mandatory_results)

    @property
    def exact(self) -> bool:
        return (
            self.relevance_eligible
            and self.identity_relation == "same"
            and self.commercially_eligible
            and not self.failed
            and not self.unknown
            and not any(result.state == "FAIL" for result in self.mandatory_results)
        )

    @property
    def commercially_eligible(self) -> bool:
        return all(
            result.state == "PASS"
            for result in self.mandatory_results
            if result.source in {"commercial_constraints", "delivery_constraints"}
        )


class ProductMatchingService:
    """Maps Phase 1 requirements to catalogue evidence and ranks verified matches."""

    def __init__(
        self,
        catalogue_service: Optional[BuyoraCatalogueService] = None,
        explanation_provider: Optional[OllamaProvider] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._catalogue_service = catalogue_service or BuyoraCatalogueService(self._settings)
        self._explanation_provider = explanation_provider or OllamaProvider(self._settings)

    async def match(
        self,
        draft: RequirementDraft,
        *,
        include_ai_explanation: bool = True,
    ) -> ProductMatchingResponse:
        draft_values = {
            "product_name": draft.product_name,
            "product_type": draft.product_type,
            "category": draft.category,
            "mandatory_requirements": draft.mandatory_requirements,
            "optional_preferences": draft.optional_preferences,
            "commercial_constraints": draft.commercial_constraints,
            "delivery_constraints": draft.delivery_constraints,
        }
        draft = RequirementDraft.model_validate(
            normalize_commercial_requirement_fields(draft_values)
        )
        catalogue_started = time.perf_counter()
        snapshot = await self._catalogue_service.fetch_products()
        catalogue_elapsed_ms = round((time.perf_counter() - catalogue_started) * 1000)
        evaluation_started = time.perf_counter()
        candidates = [
            _evaluate_product(record, *_product_relevance(record, draft), draft)
            for record in snapshot.products
        ]
        evaluation_elapsed_ms = round((time.perf_counter() - evaluation_started) * 1000)
        rankable = [
            candidate
            for candidate in candidates
            if candidate.relevance_eligible
            and candidate.commercially_eligible
            and not candidate.failed
        ]
        _rank_candidates(rankable, draft)
        exact = sorted(
            (candidate for candidate in rankable if candidate.exact),
            key=_candidate_sort_key,
        )
        near = sorted(
            (candidate for candidate in rankable if not candidate.exact),
            key=(
                _near_match_rating_sort_key
                if not exact
                else _candidate_sort_key
            ),
        )
        excluded = sorted(
            (candidate for candidate in candidates if candidate not in rankable),
            key=lambda candidate: (
                -candidate.score,
                candidate.record.product_id.casefold(),
            ),
        )
        relevant_candidates = [
            candidate for candidate in candidates if candidate.relevance_eligible
        ]
        if self._settings.DEBUG and self._settings.ENVIRONMENT.casefold() in {
            "development",
            "dev",
            "local",
            "test",
        }:
            logger.info(
                "Product matching examined %s unique catalogue products: %s exact, %s near, %s excluded.",
                len(snapshot.products),
                len(exact),
                len(near),
                len(excluded),
            )
        unverified = [candidate for candidate in near if candidate.unknown]
        best_product_id = exact[0].record.product_id if exact else None
        explanation = _deterministic_explanation(exact, near, excluded)
        explanation_elapsed_ms = 0
        if candidates and include_ai_explanation:
            explanation_started = time.perf_counter()
            explanation = await self._explain_with_ollama(
                draft,
                exact[:MAX_AI_EXPLANATION_PRODUCTS],
                near[:MAX_AI_EXPLANATION_PRODUCTS],
                excluded[:MAX_AI_EXPLANATION_PRODUCTS],
                explanation,
            )
            explanation_elapsed_ms = round(
                (time.perf_counter() - explanation_started) * 1000
            )

        logger.info(
            "Product matching stages complete: catalogue_ms=%s evaluation_ms=%s "
            "ai_explanation_ms=%s products=%s exact=%s near=%s",
            catalogue_elapsed_ms,
            evaluation_elapsed_ms,
            explanation_elapsed_ms,
            len(snapshot.products),
            len(exact),
            len(near),
        )

        unresolved = _unresolved_requirements(draft)
        return ProductMatchingResponse(
            status=(
                "matches_found"
                if exact
                else "no_exact_matches"
                if relevant_candidates
                else "no_relevant_products"
            ),
            catalogue_products_examined=len(snapshot.products),
            catalogue_products_returned=snapshot.products_returned,
            catalogue_total_reported=snapshot.total_reported,
            exact_match_count=len(exact),
            near_match_count=len(near),
            best_product_id=best_product_id,
            products=[
                self._to_response(candidate, draft)
                for candidate in exact
            ],
            near_matches=[
                self._to_response(candidate, draft)
                for candidate in near[:MAX_NEAR_MATCHES]
            ],
            excluded_products=[
                self._to_response(candidate, draft)
                for candidate in excluded[:MAX_NEAR_MATCHES]
            ],
            unverified_products=[
                self._to_response(candidate, draft)
                for candidate in unverified[:MAX_NEAR_MATCHES]
            ],
            unresolved_requirements=unresolved,
            explanation=explanation,
        )

    async def _explain_with_ollama(
        self,
        draft: RequirementDraft,
        exact: Sequence[_Candidate],
        near: Sequence[_Candidate],
        excluded: Sequence[_Candidate],
        fallback: str,
    ) -> str:
        evidence = {
            "requirements": draft.model_dump(),
            "exact_matches": [_explanation_evidence(candidate) for candidate in exact],
            "near_matches": [_explanation_evidence(candidate) for candidate in near],
            "excluded_products": [
                _explanation_evidence(candidate) for candidate in excluded
            ],
            "fallback_explanation": fallback,
        }
        try:
            return await self._explanation_provider.explain_product_matches(evidence)
        except (
            AIProviderConfigurationError,
            AIProviderInvalidResponse,
            AIProviderTimeout,
            AIProviderUnavailable,
        ) as exc:
            logger.info("Using deterministic product match explanation (%s).", type(exc).__name__)
            return fallback

    @staticmethod
    def _to_response(candidate: _Candidate, draft: RequirementDraft) -> MatchedProduct:
        record = candidate.record
        quantity = _parse_quantity(draft.commercial_constraints.get("quantity"))
        estimated_total_decimal = None
        if candidate.effective_price is not None and quantity is not None:
            estimated_total_decimal = (
                Decimal(str(candidate.effective_price)) * Decimal(str(quantity))
            )
        estimated_total = (
            float(estimated_total_decimal)
            if estimated_total_decimal is not None
            else None
        )
        explanation = candidate.explanation or _candidate_explanation(candidate)
        return MatchedProduct(
            product_id=record.product_id,
            name=record.name,
            brand=record.brand,
            category=record.category,
            description=record.description,
            price=record.price,
            discount_price=record.discount_price,
            effective_price=candidate.effective_price,
            currency=_catalogue_currency(record),
            images=record.images,
            specifications=record.specifications,
            stock=record.stock,
            delivery_days=record.delivery_days,
            rating=record.rating,
            reviews=record.reviews,
            mandatory_results=candidate.mandatory_results,
            preference_results=candidate.preference_results,
            match_score=candidate.score,
            rating_confidence=candidate.rating_confidence,
            bayesian_rating=candidate.bayesian_rating,
            estimated_total=estimated_total,
            estimated_total_note=(
                f"Estimated product total for {quantity:g} "
                f"{'unit' if quantity == 1 else 'units'}; excludes taxes and delivery."
                if estimated_total is not None and quantity is not None
                else None
            ),
            missing_attributes=candidate.missing_attributes,
            explanation=explanation,
        )


def _evaluate_product(
    record: CatalogueProduct,
    relevance: float,
    relevance_eligible: bool,
    identity_relation: str,
    draft: RequirementDraft,
) -> _Candidate:
    identity_result, category_result = _product_relevance_results(record, draft)
    mandatory_results: list[ConstraintEvaluation] = [
        identity_result,
        category_result,
    ]
    missing_attributes: list[str] = []
    for key, value in draft.mandatory_requirements.items():
        result = _evaluate_attribute(record, key, value, "mandatory_requirements")
        mandatory_results.append(result)
        if result.state == "UNKNOWN":
            missing_attributes.append(key)

    commercial = draft.commercial_constraints
    has_structured_price = any(
        key in commercial
        for key in (
            "min_target_unit_price",
            "minimum_target_unit_price",
            "max_target_unit_price",
            "maximum_target_unit_price",
            "target_unit_price",
            "total_budget",
        )
    )
    structured_results, price_preferences = _evaluate_structured_prices(record, draft)
    mandatory_results.extend(structured_results)
    budget = _budget_requirement(draft) if not has_structured_price and not commercial.get("price_type") else None
    if budget is not None:
        budget_result = _evaluate_budget(
            record,
            budget,
            draft.commercial_constraints.get("currency"),
        )
        mandatory_results.append(budget_result)
        if budget_result.state == "UNKNOWN":
            missing_attributes.append("budget")
    elif not has_structured_price and not commercial.get("price_type") and any(
        key in draft.commercial_constraints
        for key in ("budget", "minimum_budget", "maximum_budget")
    ):
        stated_budget = "; ".join(
            f"{key}: {value}"
            for key, value in draft.commercial_constraints.items()
            if key in {"budget", "minimum_budget", "maximum_budget"}
        )
        mandatory_results.append(
            ConstraintEvaluation(
                source="commercial_constraints",
                attribute="budget",
                requirement=stated_budget,
                state="UNKNOWN",
                reason="The stated budget could not be interpreted safely.",
            )
        )
        missing_attributes.append("budget")

    quantity = _parse_quantity(draft.commercial_constraints.get("quantity"))
    if draft.commercial_constraints.get("quantity") and quantity is None:
        mandatory_results.append(
            ConstraintEvaluation(
                source="commercial_constraints",
                attribute="quantity",
                requirement=draft.commercial_constraints["quantity"],
                state="UNKNOWN",
                reason="The requested purchase quantity could not be interpreted safely.",
            )
        )
        missing_attributes.append("quantity")
    elif quantity is not None:
        requested_stock_unit = _normalize_text(
            draft.commercial_constraints.get("unit", "")
        )
        if requested_stock_unit not in {
            "unit",
            "units",
            "piece",
            "pieces",
            "pc",
            "pcs",
            "item",
            "items",
        }:
            state = "UNKNOWN"
            reason = (
                "The catalogue does not specify a stock unit that can be compared "
                "with the requested unit."
            )
        elif record.stock is None:
            state = "UNKNOWN"
            reason = "Catalogue stock information is not specified."
        elif record.stock < quantity:
            state = "FAIL"
            reason = f"Only {record.stock} units are listed in stock; {quantity:g} requested."
        else:
            state = "PASS"
            reason = f"{record.stock} units are listed in stock; {quantity:g} requested."
        mandatory_results.append(
            ConstraintEvaluation(
                source="commercial_constraints",
                attribute="stock",
                requirement=f"At least {quantity:g} units available",
                state=state,
                reason=reason,
                evidence=[
                    f"stock: {record.stock}"
                    if record.stock is not None
                    else "stock: not provided"
                ],
            )
        )
        if state == "UNKNOWN":
            missing_attributes.append("stock")

    for key, value in draft.delivery_constraints.items():
        result = _evaluate_delivery(record, key, value)
        mandatory_results.append(result)
        if result.state == "UNKNOWN":
            missing_attributes.append(f"delivery {key}")

    preference_results = [
        _evaluate_attribute(record, key, value, "optional_preferences", preference=True)
        for key, value in draft.optional_preferences.items()
    ]
    preference_results.extend(price_preferences)
    rating_confidence, bayesian_rating = _rating_evidence(record)
    candidate = _Candidate(
        record=record,
        relevance=relevance,
        relevance_eligible=relevance_eligible,
        identity_relation=identity_relation,
        mandatory_results=mandatory_results,
        preference_results=preference_results,
        missing_attributes=list(dict.fromkeys(missing_attributes)),
        rating_confidence=rating_confidence,
        bayesian_rating=bayesian_rating,
        effective_price=_effective_price(record),
        explanation="",
    )
    candidate.explanation = _candidate_explanation(candidate)
    return candidate


def _evaluate_attribute(
    record: CatalogueProduct,
    key: str,
    requirement: str,
    source: str,
    preference: bool = False,
) -> ConstraintEvaluation | PreferenceEvaluation:
    normalized_key = _normalize_key(key)
    values = _attribute_values(record, key)
    required = requirement.strip()
    evidence = [f"{source}: {value}" for value, source in values]
    if not values:
        state, reason, evidence = _evaluate_unstructured_attribute(
            record,
            normalized_key,
            required,
        )
    elif normalized_key in {"processor", "chip"}:
        required_family = _processor_family(required)
        actual_families = [
            _processor_family(value)
            for value, _ in values
            if _processor_family(value)
        ]
        if not actual_families:
            state = "UNKNOWN"
            reason = "Catalogue text does not identify a specific processor family."
        elif required_family and any(
            family != required_family for family in actual_families
        ):
            state = "FAIL"
            reason = "Catalogue data confirms a different processor family."
        elif required_family and all(
            family == required_family for family in actual_families
        ):
            state = "PASS"
            reason = "Catalogue data confirms the requested processor family."
        else:
            state = "UNKNOWN"
            reason = "The requested processor family could not be interpreted safely."
    elif normalized_key == "model":
        required_model = _model_value(required)
        actual_models = [
            model
            for value, value_source in values
            if (model := _model_value(value, value_source)) is not None
        ]
        if not actual_models:
            state = "UNKNOWN"
            reason = "The catalogue does not specify the product model clearly enough to verify it."
        elif any(model != required_model for model in actual_models):
            state = "FAIL"
            reason = "Catalogue data confirms a different product model."
        else:
            state = "PASS"
            reason = "Catalogue data confirms the requested product model."
    else:
        parsed_requirement = _parse_numeric_requirement(required)
        if parsed_requirement is not None:
            if normalized_key in {"storage", "storage_capacity"} and "ssd" in required.casefold():
                actual_text = " ".join(
                    f"{value} {source}" for value, source in values
                ).casefold()
                storage_kind = _storage_kind(actual_text)
                if storage_kind != "ssd":
                    if storage_kind == "hdd":
                        state = "FAIL"
                        reason = "The catalogue confirms hard-drive storage, not the required SSD."
                    else:
                        state = "UNKNOWN"
                        reason = "Storage capacity is listed, but SSD type is not verified."
                    return _constraint_or_preference(
                        preference,
                        source,
                        key,
                        required,
                        state,
                        reason,
                        evidence,
                    )
            numeric_values = [
                parsed
                for raw_value, raw_key in values
                if (
                    parsed := _parse_single_numeric(
                        f"{raw_value} {raw_key}",
                        expected_unit=_requirement_unit(parsed_requirement),
                    )
                )
                is not None
            ]
            if not numeric_values:
                state = "UNKNOWN"
                reason = "The attribute is listed, but its numeric value or unit cannot be verified."
            else:
                comparisons = [
                    _numeric_requirement_passes(parsed_requirement, actual)
                    for actual in numeric_values
                ]
                state = "FAIL" if not all(comparisons) else "PASS"
                reason = (
                    f"Catalogue value {numeric_values[0].value:g} {numeric_values[0].unit or ''} "
                    f"{'satisfies' if state == 'PASS' else 'does not satisfy'} the stated requirement."
                )
        elif _is_exclusion(required):
            excluded_terms = _exclusion_terms(required)
            actual_text = " ".join(value for value, _ in values).casefold()
            if excluded_terms and any(term in actual_text for term in excluded_terms):
                state = "FAIL"
                reason = "Catalogue data contains an attribute the customer explicitly excluded."
            elif any(_explicitly_confirms_absence(value, excluded_terms) for value, _ in values):
                state = "PASS"
                reason = "Catalogue data explicitly confirms the excluded attribute is absent."
            else:
                state = "UNKNOWN"
                reason = "The catalogue does not verify whether the excluded attribute is absent."
        else:
            normalized_required = _normalize_text(required)
            normalized_values = [_normalize_text(value) for value, _ in values]
            if normalized_key == "display_type":
                required_display_type = _display_type(required)
                actual_display_types = {
                    parsed
                    for value, _ in values
                    if (parsed := _display_type(value)) is not None
                }
                if not actual_display_types:
                    state = "UNKNOWN"
                    reason = "The catalogue does not specify the display type."
                elif any(
                    actual_type != required_display_type
                    for actual_type in actual_display_types
                ):
                    state = "FAIL"
                    reason = "Catalogue data confirms a different display type."
                elif required_display_type in actual_display_types:
                    state = "PASS"
                    reason = "Catalogue data confirms the requested display type."
                else:
                    state = "FAIL"
                    reason = "Catalogue data confirms a different display type."
            elif _is_generic_required(required):
                actual_truth = any(_is_truthy(value) for value, _ in values)
                state = "PASS" if actual_truth else "FAIL"
                reason = (
                    "The catalogue confirms this feature."
                    if actual_truth
                    else "The catalogue lists this feature as unavailable."
                )
            elif normalized_key in {"brand", "brands", "preferred_brand", "preferred_brands"} and any(
                _normalize_text(brand) in actual
                for actual in normalized_values
                for brand in _split_values(required)
            ):
                state = "PASS"
                reason = "The catalogue brand matches one of the preferred brands."
            elif any(
                normalized_required == actual
                or normalized_required in actual
                or actual in normalized_required
                for actual in normalized_values
                if actual
            ):
                if any(
                    actual
                    and normalized_required not in actual
                    and actual not in normalized_required
                    and actual != normalized_required
                    for actual in normalized_values
                ):
                    state = "FAIL"
                    reason = "Catalogue data also confirms a different value."
                else:
                    state = "PASS"
                    reason = "Catalogue data confirms the stated preference or requirement."
            else:
                state = "FAIL" if normalized_values else "UNKNOWN"
                reason = (
                    "The catalogue lists this attribute, but its value differs from the request."
                    if normalized_values
                    else "The catalogue does not specify this attribute clearly enough to verify it."
                )

    if preference:
        return PreferenceEvaluation(
            attribute=key,
            preference=requirement,
            state=state,
            reason=reason,
            evidence=evidence,
        )
    return ConstraintEvaluation(
        source=source,
        attribute=key,
        requirement=requirement,
        state=state,
        reason=reason,
        evidence=evidence,
    )


def _attribute_values(record: CatalogueProduct, key: str) -> list[tuple[str, str]]:
    normalized_key = _normalize_key(key)
    aliases = _ATTRIBUTE_ALIASES.get(normalized_key, (normalized_key.replace("_", " "),))
    values: list[tuple[str, str]] = []
    direct_values: dict[str, Any] = {
        "brand": record.brand,
        "category": record.category,
        "name": record.name,
        "product_name": record.name,
        "stock": record.stock,
        "delivery_days": record.delivery_days,
    }
    if normalized_key == "category" and record.category_tag:
        direct_values["category_tag"] = record.category_tag
    if normalized_key in direct_values and direct_values[normalized_key] is not None:
        values.append((str(direct_values[normalized_key]), normalized_key))

    structured_fields = [
        (f"specifications.{spec_key}", spec_value)
        for spec_key, spec_value in _flatten_specs(record.specifications)
    ]
    metadata = record.model_extra or {}
    structured_fields.extend(
        (f"metadata.{spec_key}", spec_value)
        for spec_key, spec_value in _flatten_specs(metadata)
    )
    for spec_key, spec_value in structured_fields:
        normalized_spec_key = _normalize_key(spec_key.rsplit(".", 1)[-1])
        if any(
            _matches_spec_key(normalized_spec_key, _normalize_key(alias))
            for alias in aliases
        ):
            values.append((str(spec_value), spec_key))

    for source, text in _catalogue_text_sources(record):
        for extracted in _extract_explicit_attribute(normalized_key, text):
            values.append((extracted, source))
    return list(dict.fromkeys(values))


def _matches_spec_key(spec_key: str, alias: str) -> bool:
    return (
        spec_key == alias
        or spec_key.startswith(f"{alias}_")
        or spec_key.endswith(f"_{alias}")
    )


def _catalogue_currency(record: CatalogueProduct) -> Optional[str]:
    if record.currency and record.currency.strip():
        return record.currency.strip().upper()
    for key, value in _flatten_specs(record.specifications):
        if _normalize_key(key.rsplit(".", 1)[-1]) in {"currency", "currency_code"}:
            currency = str(value).strip().upper()
            return currency or None
    for key, value in _flatten_specs(record.model_extra or {}):
        if _normalize_key(key.rsplit(".", 1)[-1]) in {"currency", "currency_code"}:
            currency = str(value).strip().upper()
            return currency or None
    return None


def _catalogue_text_sources(record: CatalogueProduct) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    for field, value in (
        ("name", record.name),
        ("brand", record.brand),
        ("category", record.category),
        ("description", record.description),
        ("categoryTag", record.category_tag),
    ):
        if value:
            sources.append((field, value))
    sources.extend(
        (f"specifications.{key}", str(value))
        for key, value in _flatten_specs(record.specifications)
    )
    sources.extend(
        (f"metadata.{key}", str(value))
        for key, value in _flatten_specs(record.model_extra or {})
    )
    return sources


def _extract_explicit_attribute(key: str, text: str) -> list[str]:
    patterns = {
        "processor": (
            r"\b(?:intel\s+)?(?:core\s+)?i[3579](?:[- ]?[a-z0-9]+)?\b|"
            r"\b(?:amd\s+)?ryzen\s+[3579](?:\s+[a-z0-9-]+)?\b"
        ),
        "ram": (
            r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\s*(?:of\s+)?(?:ram|memory)\b|"
            r"\b(?:ram|memory)\s*(?:of\s*)?\d+(?:[.,]\d+)?\s*(?:gb|tb)\b"
        ),
        "storage": (
            r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\s*"
            r"(?:ssd|hdd|nvme|solid[\s-]*state(?:\s+drive)?|hard[\s-]*disk(?:\s+drive)?)\b"
        ),
        "display_size": r"\b\d+(?:[.,]\d+)?\s*[- ]?inch(?:es)?\b",
        "display_type": (
            r"\b(?:full[\s-]+hd(?:\s*\+|\s+plus)?|fhd(?:\s*\+|\s+plus)?|"
            r"qhd(?:\s*\+|\s+plus)?|uhd|hd(?:\s*\+|\s+plus)?|4k|2k)"
            r"(?=[^a-z0-9]|$)"
        ),
        "model": r"\bxps\s*\d+\b",
    }
    pattern = patterns.get(key)
    if not pattern:
        return []
    return list(dict.fromkeys(match.group(0) for match in re.finditer(pattern, text, re.IGNORECASE)))


def _processor_family(value: str) -> Optional[str]:
    intel = re.search(r"\b(?:core\s+)?i([3579])\b", value, re.IGNORECASE)
    if intel:
        return f"i{intel.group(1)}"
    ryzen = re.search(r"\bryzen\s+([3579])\b", value, re.IGNORECASE)
    if ryzen:
        return f"ryzen {ryzen.group(1)}"
    return None


def _storage_kind(value: str) -> Optional[str]:
    normalized = _normalize_text(value)
    if re.search(r"\b(?:hdd|hard disk|hard drive)\b", normalized):
        return "hdd"
    if re.search(r"\b(?:ssd|nvme|solid state drive)\b", normalized):
        return "ssd"
    return None


def _evaluate_unstructured_attribute(
    record: CatalogueProduct,
    key: str,
    required: str,
) -> tuple[ConstraintState, str, list[str]]:
    sources = _catalogue_text_sources(record)
    text = " ".join(value for _, value in sources).casefold()
    if key == "display_type":
        required_display_type = _display_type(required)
        actual_display_type = _display_type(text)
        if actual_display_type is None:
            return "UNKNOWN", "The catalogue does not specify the display type.", []
        if actual_display_type == required_display_type:
            return (
                "PASS",
                "Catalogue text confirms the requested display type.",
                [
                    f"{source}: {value}"
                    for source, value in sources
                    if _display_type(value) == required_display_type
                ],
            )
        return "FAIL", "Catalogue text confirms a different display type.", []

    if key == "processor":
        return (
            "UNKNOWN",
            "Catalogue text does not identify a specific processor family.",
            [],
        )

    normalized_required = _normalize_text(required)
    for source, value in sources:
        if normalized_required and normalized_required in _normalize_text(value):
            return (
                "PASS",
                "Catalogue text explicitly confirms this value.",
                [f"{source}: {value}"],
            )
    return (
        "UNKNOWN",
        "The catalogue does not specify this attribute clearly enough to verify it.",
        [],
    )


def _display_type(value: str) -> Optional[str]:
    match = re.search(
        r"\b(?P<full>full[\s-]+hd)(?P<fullplus>\s*\+|\s+plus)?(?=\W|$)|"
        r"\b(?P<base>FHD|QHD|UHD|HD)(?P<plus>\s*\+|\s+plus)?(?=\W|$)|"
        r"\b(?P<resolution>4K|2K)\b",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    if match.group("resolution"):
        return match.group("resolution").upper()
    if match.group("full"):
        return "FHD+" if match.group("fullplus") else "FHD"
    base = match.group("base").upper()
    plus = bool(match.group("plus"))
    return f"{base}+" if plus else base


def _model_value(value: str, source: str = "") -> Optional[str]:
    match = re.search(r"\bxps\s*\d+\b", value, re.IGNORECASE)
    if match:
        return _normalize_text(match.group(0))
    if source.rsplit(".", 1)[-1].casefold() in {"model", "product_model"}:
        normalized = _normalize_text(value)
        return normalized or None
    return None


def _flatten_specs(
    specifications: dict[str, Any],
    prefix: str = "",
) -> list[tuple[str, Any]]:
    flattened: list[tuple[str, Any]] = []
    for key, value in specifications.items():
        path = f"{prefix} {key}".strip()
        if isinstance(value, dict):
            flattened.extend(_flatten_specs(value, path))
        elif value is not None and not isinstance(value, (list, tuple, set)):
            flattened.append((path, value))
        elif isinstance(value, (list, tuple, set)):
            flattened.append((path, ", ".join(str(item) for item in value)))
    return flattened


def _parse_numeric_requirement(value: str) -> Optional[NumericRequirement]:
    text = _normalized_numeric_text(value)
    range_match = re.search(
        rf"{_NUMBER}\s*([a-zA-Z]+)?\s*(?:to|[-–—])\s*{_NUMBER}\s*([a-zA-Z]+)?",
        text,
    )
    if range_match:
        first = _numeric_value(range_match.group(1), range_match.group(2))
        second = _numeric_value(range_match.group(3), range_match.group(4))
        if first and second:
            first, second = _convert_pair(first, second)
            if first and second:
                return NumericRequirement(lower=first, upper=second)
    number_match = re.search(rf"(?:>=|≥|at least|minimum|min\.?)\s*{_NUMBER}\s*([a-zA-Z]+)?", text)
    if number_match:
        numeric = _numeric_value(number_match.group(1), number_match.group(2))
        return NumericRequirement(lower=numeric) if numeric else None
    number_match = re.search(rf"(?:<=|≤|at most|up to|maximum|max\.?|below|under|within|less than)\s*{_NUMBER}\s*([a-zA-Z]+)?", text)
    if number_match:
        numeric = _numeric_value(number_match.group(1), number_match.group(2))
        return NumericRequirement(upper=numeric) if numeric else None
    number_match = re.search(rf"{_NUMBER}\s*([a-zA-Z]+)?", text)
    if number_match:
        numeric = _numeric_value(number_match.group(1), number_match.group(2))
        return NumericRequirement(exact=numeric) if numeric else None
    return None


def _parse_single_numeric(
    value: str,
    expected_unit: Optional[str] = None,
) -> Optional[NumericValue]:
    text = _normalized_numeric_text(value)
    match = re.search(rf"{_NUMBER}\s*([a-zA-Z]+)?", text)
    if not match:
        return None
    numeric = _numeric_value(match.group(1), match.group(2))
    if numeric is None:
        return None
    if numeric.unit is None and expected_unit:
        return NumericValue(numeric.value, expected_unit)
    return numeric


def _numeric_value(number: Optional[str], unit: Optional[str]) -> Optional[NumericValue]:
    if number is None:
        return None
    try:
        value = float(number.replace(",", "."))
    except ValueError:
        return None
    normalized_unit = unit.casefold() if unit else None
    if normalized_unit:
        normalized_unit = _UNIT_ALIASES.get(normalized_unit, normalized_unit)
    if normalized_unit in _UNIT_FACTORS:
        base_unit, factor = _UNIT_FACTORS[normalized_unit]
        return NumericValue(value * factor, base_unit)
    return NumericValue(value, None)


def _convert_pair(
    first: NumericValue,
    second: NumericValue,
) -> tuple[Optional[NumericValue], Optional[NumericValue]]:
    if first.unit == second.unit:
        return first, second
    if first.unit and second.unit:
        return None, None
    unit = first.unit or second.unit
    return (
        NumericValue(first.value, unit),
        NumericValue(second.value, unit),
    )


def _requirement_unit(requirement: NumericRequirement) -> Optional[str]:
    for value in (requirement.lower, requirement.upper, requirement.exact):
        if value and value.unit:
            return value.unit
    return None


def _numeric_requirement_passes(
    requirement: NumericRequirement,
    actual: NumericValue,
) -> bool:
    bounds = [value for value in (requirement.lower, requirement.upper, requirement.exact) if value]
    if any(bound.unit and actual.unit and bound.unit != actual.unit for bound in bounds):
        return False
    if requirement.lower and actual.value < requirement.lower.value:
        return False
    if requirement.upper and actual.value > requirement.upper.value:
        return False
    if requirement.exact and not _approximately_equal(actual.value, requirement.exact.value):
        return False
    return True


def _budget_requirement(draft: RequirementDraft) -> Optional[NumericRequirement]:
    constraints = draft.commercial_constraints
    if "minimum_budget" in constraints or "maximum_budget" in constraints:
        lower = _parse_single_numeric(constraints.get("minimum_budget", ""))
        upper = _parse_single_numeric(constraints.get("maximum_budget", ""))
        if (
            ("minimum_budget" in constraints and lower is None)
            or ("maximum_budget" in constraints and upper is None)
        ):
            return None
        if lower is None and upper is None:
            return None
        return NumericRequirement(
            lower=NumericValue(lower.value, None) if lower else None,
            upper=NumericValue(upper.value, None) if upper else None,
        )
    budget = constraints.get("budget")
    if not budget:
        return None
    parsed = _parse_numeric_requirement(budget)
    if parsed:
        if parsed.exact:
            return NumericRequirement(upper=NumericValue(parsed.exact.value, None))
        return NumericRequirement(
            lower=NumericValue(parsed.lower.value, None) if parsed.lower else None,
            upper=NumericValue(parsed.upper.value, None) if parsed.upper else None,
            exact=NumericValue(parsed.exact.value, None) if parsed.exact else None,
        )
    return None


def _decimal_amount(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _evaluate_structured_prices(
    record: CatalogueProduct,
    draft: RequirementDraft,
) -> tuple[list[ConstraintEvaluation], list[PreferenceEvaluation]]:
    constraints = draft.commercial_constraints
    mandatory: list[ConstraintEvaluation] = []
    preferences: list[PreferenceEvaluation] = []
    minimum = _decimal_amount(
        constraints.get("min_target_unit_price")
        or constraints.get("minimum_target_unit_price")
    )
    maximum_raw = constraints.get(
        "max_target_unit_price",
        constraints.get("maximum_target_unit_price"),
    )
    maximum_requested = any(
        key in constraints
        for key in ("max_target_unit_price", "maximum_target_unit_price")
    )
    target = _decimal_amount(constraints.get("target_unit_price"))
    total_budget = _decimal_amount(constraints.get("total_budget"))
    required_currency = constraints.get("currency")
    actual_price = _effective_price_decimal(record)
    actual_currency = _catalogue_currency(record)

    def evaluation(
        amount_name: str,
        expected: Decimal,
        actual: Optional[Decimal],
        *,
        total: bool = False,
    ) -> ConstraintEvaluation:
        if actual is None:
            state = "UNKNOWN"
            reason = "The catalogue does not specify a usable current selling price."
        elif not required_currency or not actual_currency:
            state = "UNKNOWN"
            reason = "The requested or catalogue currency is missing, so prices cannot be compared safely."
        elif required_currency.casefold() != actual_currency.casefold():
            state = "UNKNOWN"
            reason = "The catalogue price uses a different currency and no conversion is available."
        else:
            if total:
                passes = actual <= expected
            elif amount_name == "min_target_unit_price":
                passes = actual >= expected
            else:
                passes = actual <= expected
            state = "PASS" if passes else "FAIL"
            reason = (
                "The actual catalogue price satisfies this confirmed price boundary."
                if passes
                else "The actual catalogue price violates this confirmed price boundary."
            )
        evidence = [
            f"effective catalogue unit price: {actual_price if actual_price is not None else 'not provided'}",
            f"catalogue currency: {actual_currency or 'not provided'}",
            f"requested currency: {required_currency or 'not provided'}",
        ]
        if total:
            evidence.append(
                f"catalogue total for requested quantity: {actual if actual is not None else 'not provided'}"
            )
        return ConstraintEvaluation(
            source="commercial_constraints",
            attribute=amount_name,
            requirement=format(expected, "f"),
            state=state,
            reason=reason,
            evidence=evidence,
        )

    if minimum is not None:
        mandatory.append(evaluation("min_target_unit_price", minimum, actual_price))
    if maximum_requested:
        mandatory.append(
            _verify_maximum_budget(
                record,
                maximum_raw,
                required_currency,
                price_clarification=constraints.get("price_clarification"),
            )
        )
    if total_budget is not None:
        quantity = _decimal_amount(constraints.get("quantity"))
        total_cost = (
            actual_price * quantity
            if actual_price is not None and quantity is not None
            else None
        )
        mandatory.append(evaluation("total_budget", total_budget, total_cost, total=True))
    elif constraints.get("price_type") == "ambiguous_budget":
        mandatory.append(
            ConstraintEvaluation(
                source="commercial_constraints",
                attribute="budget",
                requirement=constraints.get("budget", ""),
                state="UNKNOWN",
                reason=constraints.get(
                    "price_clarification",
                    "The budget scope must be clarified before matching.",
                ),
            )
        )
    if target is not None:
        if actual_price is None or not required_currency or not actual_currency:
            preferences.append(
                PreferenceEvaluation(
                    attribute="target_unit_price",
                    preference=format(target, "f"),
                    state="UNKNOWN",
                    reason="The target price preference cannot be compared with the catalogue price.",
                )
            )
        elif required_currency.casefold() != actual_currency.casefold():
            preferences.append(
                PreferenceEvaluation(
                    attribute="target_unit_price",
                    preference=format(target, "f"),
                    state="UNKNOWN",
                    reason="The catalogue currency differs; no conversion was applied.",
                )
            )
        else:
            preference_type = constraints.get("price_type", "exact")
            preferences.append(
                PreferenceEvaluation(
                    attribute="target_unit_price",
                    preference=format(target, "f"),
                    state="PASS",
                    reason=(
                        f"The catalogue price is ranked as a {'soft approximate' if preference_type == 'approximate' else 'target'} preference; it is not a hard limit."
                    ),
                    evidence=[
                        f"effective catalogue unit price: {actual_price}",
                        f"currency: {actual_currency}",
                    ],
                )
            )
    return mandatory, preferences


def _evaluate_budget(
    record: CatalogueProduct,
    requirement: NumericRequirement,
    required_currency: Optional[str] = None,
) -> ConstraintEvaluation:
    if (
        requirement.upper is not None
        and requirement.lower is None
        and requirement.exact is None
    ):
        return _verify_maximum_budget(
            record,
            requirement.upper.value,
            required_currency,
        )

    price = _effective_price(record)
    catalogue_currency = _catalogue_currency(record)
    if price is None:
        state = "UNKNOWN"
        reason = "The catalogue does not specify a usable current selling price."
    elif not catalogue_currency:
        state = "UNKNOWN"
        reason = "The catalogue price currency is not specified, so it cannot be verified against the requested currency."
    elif not required_currency:
        state = "UNKNOWN"
        reason = "The requested budget currency is not specified, so it cannot be compared safely."
    elif required_currency.casefold() != catalogue_currency.casefold():
        state = "UNKNOWN"
        reason = "The catalogue price uses a different currency and no currency conversion is available."
    else:
        passes = _numeric_requirement_passes(requirement, NumericValue(price, None))
        state = "PASS" if passes else "FAIL"
        reason = (
            f"Current selling price {price:g} {catalogue_currency} satisfies the stated budget."
            if passes
            else f"Current selling price {price:g} {catalogue_currency} is outside the stated budget."
        )
    return ConstraintEvaluation(
        source="commercial_constraints",
        attribute="budget",
        requirement=_format_numeric_requirement(requirement),
        state=state,
        reason=reason,
        evidence=[
            f"effective catalogue price: {price}"
            if price is not None
            else "effective catalogue price: not provided",
            f"catalogue currency: {catalogue_currency or 'not provided'}",
            f"requested currency: {required_currency or 'not provided'}",
        ],
    )


def _verify_maximum_budget(
    record: CatalogueProduct,
    requested_value: Any,
    requested_currency: Optional[str],
    *,
    price_clarification: Optional[str] = None,
) -> ConstraintEvaluation:
    expected = _decimal_amount(str(requested_value)) if requested_value is not None else None
    effective_price = _effective_price_decimal(record)
    product_currency = _catalogue_currency(record)

    if price_clarification:
        state = "UNKNOWN"
        reason = price_clarification
    elif expected is None:
        state = "UNKNOWN"
        reason = "The maximum unit price is missing or is not a usable positive amount."
    elif effective_price is None:
        state = "UNKNOWN"
        reason = "The catalogue does not specify a usable effective selling price."
    elif not requested_currency or not product_currency:
        state = "UNKNOWN"
        reason = "The requested or catalogue currency is missing, so prices cannot be compared safely."
    elif requested_currency.strip().casefold() != product_currency.casefold():
        state = "UNKNOWN"
        reason = "The catalogue price uses a different currency and no conversion is available."
    elif effective_price <= expected:
        state = "PASS"
        reason = "The effective catalogue unit price is at or below the maximum unit price."
    else:
        state = "FAIL"
        reason = "The effective catalogue unit price exceeds the maximum unit price."

    return ConstraintEvaluation(
        source="commercial_constraints",
        attribute="max_target_unit_price",
        requirement=format(expected, "f") if expected is not None else str(requested_value or ""),
        state=state,
        reason=reason,
        evidence=[
            f"effective catalogue unit price: {effective_price if effective_price is not None else 'not provided'}",
            f"catalogue currency: {product_currency or 'not provided'}",
            f"requested currency: {requested_currency or 'not provided'}",
        ],
    )


def _evaluate_delivery(
    record: CatalogueProduct,
    key: str,
    value: str,
) -> ConstraintEvaluation:
    if key.casefold() in {"location", "destination"}:
        return ConstraintEvaluation(
            source="delivery_constraints",
            attribute=key,
            requirement=value,
            state="UNKNOWN",
            reason="The catalogue does not provide destination-specific delivery coverage.",
        )
    parsed = _parse_numeric_requirement(value)
    if parsed is None:
        return ConstraintEvaluation(
            source="delivery_constraints",
            attribute=key,
            requirement=value,
            state="UNKNOWN",
            reason="The delivery timing could not be interpreted using catalogue data.",
        )
    if record.delivery_days is None:
        state = "UNKNOWN"
        reason = "The catalogue does not specify a delivery estimate."
    else:
        requested = parsed
        unit = _requirement_unit(requested)
        if unit in {"hour", "minute", "week"}:
            converted = _convert_delivery_requirement(requested)
            requested = converted or requested
        if _numeric_requirement_passes(
            requested,
            NumericValue(record.delivery_days, "day"),
        ):
            state = "PASS"
            reason = f"The listed delivery estimate is {record.delivery_days:g} days."
        else:
            state = "FAIL"
            reason = f"The listed delivery estimate is {record.delivery_days:g} days and misses the request."
    return ConstraintEvaluation(
        source="delivery_constraints",
        attribute=key,
        requirement=value,
        state=state,
        reason=reason,
    )


def _convert_delivery_requirement(
    requirement: NumericRequirement,
) -> Optional[NumericRequirement]:
    def as_days(value: Optional[NumericValue]) -> Optional[NumericValue]:
        if value is None:
            return None
        if value.unit == "day":
            return value
        factor = {"hour": 1 / 24, "minute": 1 / 1440, "week": 7}.get(value.unit)
        return NumericValue(value.value * factor, "day") if factor else value

    return NumericRequirement(
        lower=as_days(requirement.lower),
        upper=as_days(requirement.upper),
        exact=as_days(requirement.exact),
    )


def _parse_quantity(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    match = re.search(_NUMBER, _normalized_numeric_text(value))
    if not match:
        return None
    try:
        quantity = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return quantity if quantity > 0 else None


def _effective_price_decimal(record: CatalogueProduct) -> Optional[Decimal]:
    def valid_price(value: Optional[float]) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            price = Decimal(str(value))
        except InvalidOperation:
            return None
        return price if price.is_finite() and price > 0 else None

    regular = valid_price(record.price)
    discount = valid_price(record.discount_price)
    if discount is not None and (regular is None or discount < regular):
        return discount
    return regular


def _effective_price(record: CatalogueProduct) -> Optional[float]:
    price = _effective_price_decimal(record)
    return float(price) if price is not None else None


def _rating_evidence(record: CatalogueProduct) -> tuple[Optional[float], Optional[float]]:
    if record.rating is None or record.reviews is None:
        return None, None
    confidence = record.reviews / (record.reviews + BAYESIAN_MINIMUM_REVIEW_CONFIDENCE)
    bayesian = (
        record.rating * record.reviews
        + BAYESIAN_GLOBAL_AVERAGE_RATING * BAYESIAN_MINIMUM_REVIEW_CONFIDENCE
    ) / (record.reviews + BAYESIAN_MINIMUM_REVIEW_CONFIDENCE)
    return confidence, bayesian


def _rank_candidates(candidates: list[_Candidate], draft: RequirementDraft) -> None:
    constraints = draft.commercial_constraints
    has_structured_price = any(
        key in constraints
        for key in (
            "min_target_unit_price",
            "minimum_target_unit_price",
            "max_target_unit_price",
            "maximum_target_unit_price",
            "target_unit_price",
            "total_budget",
        )
    )
    budget = (
        _budget_requirement(draft)
        if not has_structured_price and not constraints.get("price_type")
        else None
    )
    target_price = _decimal_amount(constraints.get("target_unit_price"))
    if budget:
        pricing_candidates = [
            candidate
            for candidate in candidates
            if any(
                result.attribute == "budget" and result.state == "PASS"
                for result in candidate.mandatory_results
            )
        ]
    else:
        pricing_candidates = [candidate for candidate in candidates if candidate.exact]
        if not pricing_candidates:
            pricing_candidates = [
                candidate for candidate in candidates if not candidate.failed
            ]
        if not pricing_candidates:
            pricing_candidates = candidates
    prices = [
        _effective_price(candidate.record)
        for candidate in pricing_candidates
        if _effective_price(candidate.record) is not None
    ]
    budget_ceiling = budget.upper.value if budget and budget.upper else None
    if budget_ceiling is None and prices:
        budget_ceiling = max(prices)
    budget_floor = (
        budget.lower.value
        if budget and budget.lower
        else min(prices)
        if prices
        else None
    )

    preferred_brands = _split_brand_preferences(draft.optional_preferences)
    use_case_terms = _use_case_terms(draft)
    delivery_preference = _delivery_preference(draft)
    for candidate in candidates:
        checked = candidate.mandatory_results
        mandatory_score = (
            sum(result.state == "PASS" for result in checked) / len(checked)
            if checked
            else 0.0
        )
        brand_score = _brand_score(candidate.record, preferred_brands)
        optional_scores = [
            1.0 if result.state == "PASS" else 0.0
            for result in candidate.preference_results
            if _normalize_key(result.attribute)
            not in {
                "brand",
                "brands",
                "preferred_brand",
                "preferred_brands",
            }
            and not any(
                part in _normalize_key(result.attribute)
                for part in ("application", "use_case", "intended_use")
            )
        ]
        optional_score = (
            sum(optional_scores) / len(optional_scores)
            if optional_scores
            else 0.0
        )
        use_case_score = _text_relevance(use_case_terms, _product_text(candidate.record))
        rating_score = (
            candidate.bayesian_rating / 5
            if candidate.bayesian_rating is not None
            else 0.0
        )
        confidence_score = candidate.rating_confidence or 0.0
        price = _effective_price(candidate.record)
        budget_result = next(
            (
                result
                for result in checked
                if result.attribute in {"budget", "max_target_unit_price"}
            ),
            None,
        )
        if target_price is not None:
            if (
                price is None
                or not constraints.get("currency")
                or constraints["currency"].casefold()
                != (_catalogue_currency(candidate.record) or "").casefold()
            ):
                price_score = 0.0
            else:
                ratio = abs(Decimal(str(price)) - target_price) / target_price
                price_score = float(Decimal(1) / (Decimal(1) + ratio))
        elif (
            price is None
            or budget_ceiling is None
            or (budget is not None and (budget_result is None or budget_result.state != "PASS"))
        ):
            price_score = 0.0
        elif budget_floor is not None and budget_ceiling <= budget_floor:
            price_score = 1.0 if price == budget_ceiling else 0.0
        else:
            span = budget_ceiling - (budget_floor or 0)
            price_score = (
                max(0.0, min(1.0, (budget_ceiling - price) / span))
                if span > 0
                else 0.0
            )
        delivery_score = 0.0
        if delivery_preference:
            result = _evaluate_delivery(
                candidate.record,
                delivery_preference[0],
                delivery_preference[1],
            )
            delivery_score = 1.0 if result.state == "PASS" else 0.0

        breakdown = {
            "mandatory_satisfaction": mandatory_score,
            "product_relevance": candidate.relevance,
            "preferred_brand": brand_score,
            "optional_specifications": optional_score,
            "use_case_relevance": use_case_score,
            "customer_rating": rating_score,
            "rating_confidence": confidence_score,
            "price_competitiveness": price_score,
            "delivery_preference": delivery_score,
        }
        candidate.breakdown = breakdown
        candidate.score = sum(
            RANKING_WEIGHTS[factor] * score
            for factor, score in breakdown.items()
        )


def _brand_score(record: CatalogueProduct, preferred_brands: list[str]) -> float:
    if not preferred_brands:
        return 0.0
    if not record.brand:
        return 0.0
    brand = _normalize_text(record.brand)
    return 1.0 if any(_normalize_text(preferred) in brand for preferred in preferred_brands) else 0.0


def _split_brand_preferences(preferences: dict[str, str]) -> list[str]:
    result: list[str] = []
    for key, value in preferences.items():
        if _normalize_key(key) not in {"brand", "brands", "preferred_brand", "preferred_brands"}:
            continue
        result.extend(_split_values(value))
    return result


def _split_values(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r",|;|\bor\b|\band\b", value, flags=re.IGNORECASE)
        if item.strip()
    ]


def _use_case_terms(draft: RequirementDraft) -> list[str]:
    terms: list[str] = []
    sources = [draft.mandatory_requirements, draft.optional_preferences]
    for source in sources:
        for key, value in source.items():
            if any(part in _normalize_key(key) for part in ("application", "use_case", "intended_use")):
                terms.extend(_tokens(value))
    return terms


def _delivery_preference(draft: RequirementDraft) -> Optional[tuple[str, str]]:
    if not draft.delivery_constraints:
        return None
    for key, value in draft.delivery_constraints.items():
        if key.casefold() not in {"location", "destination"}:
            return key, value
    return None


def _product_relevance(
    record: CatalogueProduct,
    draft: RequirementDraft,
) -> tuple[float, bool, str]:
    identity_matches, relation, expected_category = _identity_relation(record, draft)
    if not identity_matches:
        return 0.0, False, relation

    category_matches = _category_is_relevant(record, draft, expected_category)
    if not category_matches:
        return 0.0, False, relation
    return (1.0 if relation == "same" else 0.75), True, relation


def _identity_relation(
    record: CatalogueProduct,
    draft: RequirementDraft,
) -> tuple[bool, str, Optional[str]]:
    request_identity = (
        _recognized_product_identity(draft.product_type or "")
        or _recognized_product_identity(draft.product_name or "")
    )
    product_identity = _recognized_product_identity(record.name or "")

    if request_identity:
        if not product_identity:
            return False, "unrelated", request_identity[2]
        requested_type, _, expected_category, related_types = request_identity
        product_type, _, product_category, product_related_types = product_identity
        if product_type == requested_type:
            relation = "same"
        elif product_type in related_types or requested_type in product_related_types:
            relation = "related"
        else:
            return False, "unrelated", expected_category
        expected_category = expected_category or product_category
    else:
        requested_terms = set(
            _tokens(draft.product_type or draft.product_name or "")
        )
        product_terms = set(_tokens(record.name or ""))
        if not requested_terms or not requested_terms.issubset(product_terms):
            return False, "unrelated", None
        relation = "same"
        expected_category = None
    return True, relation, expected_category


def _category_is_relevant(
    record: CatalogueProduct,
    draft: RequirementDraft,
    expected_category: Optional[str],
) -> bool:
    requested_category = _category_family(draft.category)
    if expected_category and requested_category and requested_category != expected_category:
        return False
    expected_category = expected_category or requested_category
    product_category = _category_family(record.category or record.category_tag)
    if expected_category:
        if product_category != expected_category:
            return False
    elif draft.category:
        if _normalize_text(draft.category) != _normalize_text(record.category or record.category_tag or ""):
            return False
    elif not record.category and not record.category_tag:
        return False
    return True


def _product_relevance_results(
    record: CatalogueProduct,
    draft: RequirementDraft,
) -> tuple[ConstraintEvaluation, ConstraintEvaluation]:
    identity_matches, identity_relation, expected_category = _identity_relation(
        record, draft
    )
    category_matches = _category_is_relevant(record, draft, expected_category)
    identity_result = ConstraintEvaluation(
        source="product_relevance",
        attribute="product_identity",
        requirement=draft.product_type or draft.product_name or "",
        state="PASS" if identity_matches else "FAIL",
        reason=(
            f"The catalogue product has a {identity_relation} product identity."
            if identity_matches
            else "The catalogue product identity is not the same as or a recognized alternative to the request."
        ),
    )
    category_result = ConstraintEvaluation(
        source="product_relevance",
        attribute="category",
        requirement=draft.category or "",
        state="PASS" if category_matches else "FAIL",
        reason=(
            f"Catalogue category {record.category or record.category_tag!r} is relevant."
            if category_matches
            else f"Catalogue category {record.category or record.category_tag!r} does not verify the requested product category."
        ),
        evidence=[
            value
            for value in (record.category, record.category_tag)
            if value
        ],
    )
    return identity_result, category_result


def _recognized_product_identity(
    value: str,
) -> Optional[tuple[str, str, str, tuple[str, ...]]]:
    normalized = f" {_normalize_text(value)} "
    for identity, aliases, category, related in _PRODUCT_IDENTITIES:
        if any(f" {_normalize_text(alias)} " in normalized for alias in aliases):
            return identity, value, category, related
    return None


def _category_family(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = _normalize_text(value)
    if not normalized:
        return None
    for alias, family in _CATEGORY_FAMILIES.items():
        if normalized == _normalize_text(alias):
            return family
    return None


def _product_text(record: CatalogueProduct) -> str:
    return " ".join(value for _, value in _catalogue_text_sources(record)).casefold()


def _text_relevance(terms: list[str], text: str) -> float:
    if not terms:
        return 0.0
    return sum(1 for term in terms if term in text.casefold()) / len(terms)


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in _GENERIC_WORDS
    ]


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _normalized_numeric_text(value: str) -> str:
    return value.replace(",", "").replace("₹", "").replace("$", "").replace("€", "").casefold()


def _is_generic_required(value: str) -> bool:
    return _normalize_text(value) in {"required", "yes", "true", "present", "must have"}


def _is_truthy(value: str) -> bool:
    return _normalize_text(value) not in {"", "no", "false", "not available", "none", "0", "not supported"}


def _is_exclusion(value: str) -> bool:
    return bool(re.search(r"\b(without|free from|must not|not contain|no )\b", value, re.IGNORECASE))


def _exclusion_terms(value: str) -> list[str]:
    match = re.search(r"\b(?:without|free from|must not contain|must not|not contain|no)\s+(.+)", value, re.IGNORECASE)
    if not match:
        return []
    return [term for term in _tokens(match.group(1)) if term not in {"be", "have"}]


def _explicitly_confirms_absence(value: str, excluded_terms: list[str]) -> bool:
    normalized = _normalize_text(value)
    return bool(excluded_terms) and all(
        term in normalized
        and any(marker in normalized for marker in ("free from", "without", "not contain", "no "))
        for term in excluded_terms
    )


def _format_numeric_requirement(requirement: NumericRequirement) -> str:
    if requirement.lower and requirement.upper:
        return (
            f"{requirement.lower.value:g}–{requirement.upper.value:g} "
            f"{requirement.lower.unit or requirement.upper.unit or ''}"
        ).strip()
    if requirement.lower:
        return f"At least {requirement.lower.value:g} {requirement.lower.unit or ''}".strip()
    if requirement.upper:
        return f"At most {requirement.upper.value:g} {requirement.upper.unit or ''}".strip()
    if requirement.exact:
        return f"{requirement.exact.value:g} {requirement.exact.unit or ''}".strip()
    return "Unresolved numeric requirement"


def _unresolved_requirements(draft: RequirementDraft) -> list[str]:
    unresolved: list[str] = []
    for key, value in draft.mandatory_requirements.items():
        if re.search(r"\d", value) and _parse_numeric_requirement(value) is None:
            unresolved.append(f"{key}: {value}")
    budget = draft.commercial_constraints.get("budget")
    if budget and _budget_requirement(draft) is None:
        unresolved.append(f"budget: {budget}")
    for key in ("minimum_budget", "maximum_budget"):
        if key in draft.commercial_constraints and _budget_requirement(draft) is None:
            unresolved.append(f"{key}: {draft.commercial_constraints[key]}")
    quantity = draft.commercial_constraints.get("quantity")
    if quantity and _parse_quantity(quantity) is None:
        unresolved.append(f"quantity: {quantity}")
    for key, value in draft.delivery_constraints.items():
        if key.casefold() not in {"location", "destination"} and _parse_numeric_requirement(value) is None:
            unresolved.append(f"delivery {key}: {value}")
    return unresolved


def _deterministic_explanation(
    exact: Sequence[_Candidate],
    near: Sequence[_Candidate],
    excluded: Sequence[_Candidate],
) -> str:
    if exact:
        best = exact[0]
        return (
            f"{best.record.name or best.record.product_id} is the highest-ranked verified match. "
            "Eligibility is determined by catalogue-backed mandatory checks; preference and "
            "rating signals only affect ranking."
        )
    if near:
        return (
            "No exact catalogue match was verified. Near matches are relevant alternatives "
            "or products with unverified attributes; ineligible products are excluded."
        )
    if excluded:
        return (
            "No exact catalogue match was found. Excluded products failed product identity, "
            "category, mandatory requirement, or commercial eligibility checks."
        )
    return "No relevant products were returned by the catalogue for this product type."


def _candidate_explanation(candidate: _Candidate) -> str:
    if not candidate.relevance_eligible:
        failed_gates = [
            result.reason
            for result in candidate.mandatory_results
            if result.source == "product_relevance" and result.state == "FAIL"
        ]
        return " ".join(failed_gates) or "Product relevance could not be established."
    if candidate.identity_relation == "related":
        return (
            "This is a related product type, not the same product identity; "
            "review the listed differences before considering it."
        )
    color_differences = [
        result
        for result in candidate.mandatory_results
        if result.state == "FAIL"
        and result.source == "mandatory_requirements"
        and _normalize_key(result.attribute) in {"color", "colour"}
    ]
    if color_differences:
        return (
            "This is a relevant product variant with a confirmed color difference: "
            + "; ".join(result.reason for result in color_differences)
        )
    passed = sum(result.state == "PASS" for result in candidate.mandatory_results)
    unknown = sum(result.state == "UNKNOWN" for result in candidate.mandatory_results)
    failed = sum(result.state == "FAIL" for result in candidate.mandatory_results)
    if failed:
        return f"{failed} mandatory requirement(s) are confirmed to fail; this product is not an exact match."
    if unknown:
        return f"{passed} mandatory requirement(s) pass; {unknown} remain unverified, so this is not an exact match."
    if candidate.mandatory_results:
        return f"All {passed} mandatory requirement(s) are confirmed by the catalogue."
    return "No mandatory product constraints were supplied; ranking is based only on available evidence."


def _explanation_evidence(candidate: _Candidate) -> dict[str, Any]:
    record = candidate.record
    return {
        "product_id": record.product_id,
        "name": record.name,
        "brand": record.brand,
        "effective_price": candidate.effective_price,
        "currency": _catalogue_currency(record),
        "rating": record.rating,
        "reviews": record.reviews,
        "bayesian_rating": candidate.bayesian_rating,
        "match_score": round(candidate.score, 4),
        "mandatory_results": [
            result.model_dump() for result in candidate.mandatory_results
        ],
        "preference_results": [
            result.model_dump() for result in candidate.preference_results
        ],
        "missing_attributes": candidate.missing_attributes,
    }


def _candidate_sort_key(candidate: _Candidate) -> tuple[float, str]:
    return (-candidate.score, candidate.record.product_id.casefold())


def _near_match_rating_sort_key(candidate: _Candidate) -> tuple[float, float, float, float, str]:
    passed_constraints = sum(
        result.state == "PASS" for result in candidate.mandatory_results
    )
    return (
        -passed_constraints,
        -(candidate.bayesian_rating or 0.0),
        -(candidate.rating_confidence or 0.0),
        -candidate.score,
        candidate.record.product_id.casefold(),
    )


def _constraint_or_preference(
    preference: bool,
    source: str,
    key: str,
    requirement: str,
    state: ConstraintState,
    reason: str,
    evidence: Optional[list[str]] = None,
) -> ConstraintEvaluation | PreferenceEvaluation:
    if preference:
        return PreferenceEvaluation(
            attribute=key,
            preference=requirement,
            state=state,
            reason=reason,
            evidence=evidence or [],
        )
    return ConstraintEvaluation(
        source=source,
        attribute=key,
        requirement=requirement,
        state=state,
        reason=reason,
        evidence=evidence or [],
    )


def _approximately_equal(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, abs(right) * 1e-6)
