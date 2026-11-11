import logging

from anyio import run
from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import Settings
from app.main import app
from app.schemas.ai import RequirementDraft
from app.schemas.product import ProductMatchRequest
from app.schemas.product_matching import ProductMatchingResponse
from app.services.buyora_catalogue_service import (
    BuyoraCatalogueService,
    CatalogueProduct,
    CatalogueSnapshot,
    CatalogueInvalidResponse,
    CatalogueTimeout,
    CatalogueUnavailable,
)
from app.services.product_matching_service import (
    ProductMatchingService,
    _evaluate_structured_prices,
)
from app.api.v1.endpoints import products as products_endpoint


def _settings(url: str = "https://catalogue.example/api/products") -> Settings:
    return Settings(
        BUYORA_CATALOGUE_API_URL=url,
        CATALOGUE_API_TIMEOUT_SECONDS=5,
    )


def _product(
    product_id: str,
    *,
    name: str = "Dell XPS 13 Laptop",
    brand: str | None = "Dell",
    price: float = 89999,
    discount_price: float | None = 79999,
    stock: int | None = 3,
    currency: str | None = "INR",
    specifications: dict | None = None,
    description: str = "Dell laptop for everyday professional use.",
) -> CatalogueProduct:
    return CatalogueProduct(
        productId=product_id,
        name=name,
        brand=brand,
        category="Electronics",
        description=description,
        price=price,
        discountPrice=discount_price,
        currency=currency,
        images=["https://catalogue.example/images/xps13.jpg"],
        specifications=specifications or {},
        stock=stock,
        deliveryDays=3,
        rating=4.7,
        reviews=2245,
    )


def _draft() -> RequirementDraft:
    return RequirementDraft(
        product_name="Dell XPS 13 laptop",
        product_type="Laptop",
        category="Computers",
        mandatory_requirements={
            "brand": "Dell",
            "model": "XPS 13",
            "processor": "Intel Core i7",
            "ram": "16GB",
            "storage": "512GB SSD",
            "display_size": "13.4 inches",
            "display_type": "FHD+",
        },
        optional_preferences={"application": "everyday professional use"},
        commercial_constraints={
            "budget": "80000",
            "currency": "INR",
            "quantity": "1",
            "unit": "unit",
        },
    )


class _StaticCatalogue:
    def __init__(self, products: list[CatalogueProduct]) -> None:
        self.products = products

    async def fetch_products(self) -> CatalogueSnapshot:
        return CatalogueSnapshot(
            products=self.products,
            products_returned=len(self.products),
            total_reported=len(self.products),
        )


class _DeterministicExplanation:
    async def explain_product_matches(self, evidence: dict[str, object]) -> str:
        return str(evidence["fallback_explanation"])


def test_catalogue_service_retrieves_all_advertised_pages_and_preserves_ids():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("page") == "2":
            payload = {
                "success": True,
                "products": [
                    {"productId": "PAGE-2", "name": "Second page"},
                    {"productId": "PAGE-1", "name": "Duplicate from page one"},
                ],
            }
        else:
            payload = {
                "success": True,
                "products": [{"productId": "PAGE-1", "name": "First page"}],
                "pagination": {"page": 1, "totalPages": 2, "total": 2},
            }
        return httpx.Response(200, json=payload)

    service = BuyoraCatalogueService(
        _settings("https://catalogue.example/api/products/"),
        transport=httpx.MockTransport(handler),
    )
    snapshot = run(service.fetch_products)

    assert [product.product_id for product in snapshot.products] == ["PAGE-1", "PAGE-2"]
    assert snapshot.products_returned == 2
    assert snapshot.total_reported == 2
    assert [request.url.path for request in requests] == [
        "/api/products",
        "/api/products",
    ]
    assert requests[1].url.params.get("page") == "2"
    assert all(request.headers["cache-control"] == "no-cache" for request in requests)


def test_catalogue_service_reports_all_unpaginated_records_returned_by_api():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "count": 2,
                "products": [
                    {"productId": "REAL-1", "name": "Product one"},
                    {"productId": "REAL-2", "name": "Product two"},
                ],
            },
        )

    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    snapshot = run(service.fetch_products)

    assert snapshot.products_returned == 2
    assert snapshot.total_reported == 2
    assert snapshot.pagination_supported is False
    assert [product.product_id for product in snapshot.products] == ["REAL-1", "REAL-2"]


def test_catalogue_product_parses_specification_json_and_preserves_images_and_metadata():
    product = CatalogueProduct.model_validate(
        {
            "productId": "MAPPED",
            "name": "Dell XPS 13 Laptop",
            "brand": "Dell",
            "category": "Laptops",
            "description": "Intel Core i7 laptop",
            "price": 89999,
            "discountPrice": 79999,
            "images": ["https://catalogue.example/xps.jpg"],
            "specifications": '{"chip":"Intel Core i7","currency":"INR"}',
            "stock": 10,
            "deliveryDays": 3,
            "rating": 4.7,
            "reviews": 2245,
            "catalogueMetadata": {"source": "buyora"},
        }
    )

    assert product.product_id == "MAPPED"
    assert product.specifications == {"chip": "Intel Core i7", "currency": "INR"}
    assert product.model_extra == {"catalogueMetadata": {"source": "buyora"}}
    assert product.images == ["https://catalogue.example/xps.jpg"]
    assert product.price == 89999
    assert product.discount_price == 79999
    assert product.stock == 10
    assert product.delivery_days == 3
    assert product.rating == 4.7
    assert product.reviews == 2245


def test_catalogue_diagnostics_redact_url_secrets_and_count_unique_seed_ids(caplog):
    settings = Settings(
        BUYORA_CATALOGUE_API_URL=(
            "https://user:password@catalogue.example/api/products?token=secret"
        ),
        DEBUG=True,
        ENVIRONMENT="development",
    )
    records = [
        {"productId": f"LAPTOP-{number}", "name": f"Laptop {number}"}
        for number in range(101, 111)
    ]
    records.append({"productId": "LAPTOP-101", "name": "Duplicate"})
    service = BuyoraCatalogueService(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"success": True, "count": len(records), "products": records},
            )
        ),
    )

    with caplog.at_level(logging.INFO, logger="markettwin.buyora_catalogue"):
        snapshot = run(service.fetch_products)

    assert snapshot.products_returned == 10
    assert "https://catalogue.example/api/products" in caplog.text
    assert "LAPTOP-101, LAPTOP-102" in caplog.text
    assert "LAPTOP-110" in caplog.text
    assert "11 records" in caplog.text
    assert "10 unique products" in caplog.text
    assert "password" not in caplog.text
    assert "secret" not in caplog.text


def test_catalogue_service_rejects_invalid_api_shape():
    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"success": True, "items": []})
        ),
    )

    with pytest.raises(CatalogueUnavailable):
        run(service.fetch_products)


def test_catalogue_service_rejects_invalid_json():
    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"not-json")
        ),
    )

    with pytest.raises(CatalogueInvalidResponse):
        run(service.fetch_products)


def test_catalogue_service_rejects_catalogue_with_only_invalid_products():
    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"success": True, "products": [{"name": "Missing product ID"}]},
            )
        ),
    )

    with pytest.raises(CatalogueInvalidResponse):
        run(service.fetch_products)


def test_catalogue_service_handles_empty_catalogue_and_http_errors():
    empty_service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"success": True, "count": 0, "products": []})
        ),
    )
    empty_snapshot = run(empty_service.fetch_products)
    assert empty_snapshot.products == []
    assert empty_snapshot.products_returned == 0

    error_service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    with pytest.raises(CatalogueUnavailable):
        run(error_service.fetch_products)


def test_catalogue_service_retries_transient_gateway_failure():
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(504)
        return httpx.Response(
            200,
            json={"success": True, "products": [{"productId": "RETRIED"}]},
        )

    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    snapshot = run(service.fetch_products)

    assert attempts == 2
    assert [product.product_id for product in snapshot.products] == ["RETRIED"]


def test_catalogue_service_retries_transient_request_timeout():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("catalogue timed out", request=request)
        return httpx.Response(
            200,
            json={"success": True, "products": [{"productId": "TIMEOUT-RETRIED"}]},
        )

    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    snapshot = run(service.fetch_products)

    assert attempts == 2
    assert [product.product_id for product in snapshot.products] == ["TIMEOUT-RETRIED"]


def test_catalogue_service_does_not_retry_nontransient_http_errors():
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400)

    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(CatalogueUnavailable):
        run(service.fetch_products)

    assert attempts == 1


def test_catalogue_service_maps_timeout_to_catalogue_timeout():
    from app.services.buyora_catalogue_service import CatalogueTimeout

    attempts = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("catalogue timed out", request=request)

    service = BuyoraCatalogueService(
        _settings(),
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(CatalogueTimeout):
        run(service.fetch_products)
    assert attempts == 2


def test_matcher_returns_exact_product_with_real_price_stock_rating_and_delivery():
    specs = {
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    }
    product = _product("DELL-XPS-13", specifications=specs)
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue([product]),
        explanation_provider=_DeterministicExplanation(),
    )

    response = run(service.match, _draft())

    assert response.exact_match_count == 1
    assert response.best_product_id == "DELL-XPS-13"
    matched = response.products[0]
    assert all(item.state == "PASS" for item in matched.mandatory_results)
    assert matched.effective_price == 79999
    assert matched.estimated_total == 79999
    assert matched.stock == 3
    assert matched.rating == 4.7
    assert matched.reviews == 2245
    assert matched.delivery_days == 3


def test_purchase_quantity_is_normalized_out_of_mandatory_specifications():
    draft_data = _draft().model_dump()
    draft_data["mandatory_requirements"][" Purchase   Quantity "] = "6"

    draft = RequirementDraft.model_validate(draft_data)

    assert not any(
        key.casefold().replace(" ", "_") in {"quantity", "purchase_quantity"}
        for key in draft.mandatory_requirements
    )
    assert draft.commercial_constraints["quantity"] == "1"

    draft_data["commercial_constraints"].pop("quantity")
    normalized_without_commercial_quantity = RequirementDraft.model_validate(
        draft_data
    )
    assert (
        normalized_without_commercial_quantity.commercial_constraints["quantity"]
        == "6"
    )


@pytest.mark.parametrize(
    ("requested_quantity", "stock", "expected_state", "expected_exact_count"),
    [
        ("1", 10, "PASS", 1),
        ("6", 6, "PASS", 1),
        ("7", 6, "FAIL", 0),
        ("1", None, "UNKNOWN", 0),
    ],
)
def test_purchase_quantity_is_validated_against_catalogue_stock(
    requested_quantity,
    stock,
    expected_state,
    expected_exact_count,
):
    draft_data = _draft().model_dump()
    draft_data["commercial_constraints"]["quantity"] = requested_quantity
    draft_data["mandatory_requirements"]["quantity"] = requested_quantity
    draft = RequirementDraft.model_validate(draft_data)
    product = _product(
        f"STOCK-{requested_quantity}-{stock}",
        stock=stock,
        specifications={
            "processor": "Intel Core i7-1360P",
            "RAM": "16GB",
            "storage": "512GB SSD",
            "display size": "13.4 inches",
            "display type": "FHD+",
        },
    )
    response = run(
        ProductMatchingService(
            catalogue_service=_StaticCatalogue([product]),
            explanation_provider=_DeterministicExplanation(),
        ).match,
        draft,
    )

    matched_products = (
        response.products + response.near_matches + response.excluded_products
    )
    assert len(matched_products) == 1
    results = matched_products[0].mandatory_results
    assert [item.state for item in results if item.attribute == "stock"] == [
        expected_state
    ]
    assert not any(item.attribute.casefold() == "quantity" for item in results)
    assert response.exact_match_count == expected_exact_count
    if expected_exact_count:
        assert response.products[0].product_id == product.product_id


def test_hard_maximum_price_excludes_products_above_the_confirmed_limit():
    draft = _draft()
    draft.commercial_constraints.update(
        {
            "max_target_unit_price": "75000",
            "price_type": "maximum",
        }
    )
    product = _product("OVER-MAXIMUM", specifications={
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    })
    response = run(
        ProductMatchingService(
            catalogue_service=_StaticCatalogue([product]),
            explanation_provider=_DeterministicExplanation(),
        ).match,
        draft,
    )

    assert response.exact_match_count == 0
    assert response.products == []
    maximum_result = next(
        result
        for result in _evaluate_structured_prices(product, draft)[0]
        if result.attribute == "max_target_unit_price"
    )
    assert maximum_result.state == "FAIL"


def test_hard_minimum_price_excludes_products_below_the_confirmed_floor():
    draft = _draft()
    draft.commercial_constraints.update(
        {
            "min_target_unit_price": "85000",
            "price_type": "minimum",
        }
    )
    product = _product("BELOW-MINIMUM", specifications={
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    })
    response = run(
        ProductMatchingService(
            catalogue_service=_StaticCatalogue([product]),
            explanation_provider=_DeterministicExplanation(),
        ).match,
        draft,
    )

    assert response.exact_match_count == 0
    assert response.products == []
    minimum_result = next(
        result
        for result in _evaluate_structured_prices(product, draft)[0]
        if result.attribute == "min_target_unit_price"
    )
    assert minimum_result.state == "FAIL"


def test_target_price_remains_a_soft_ranking_preference():
    draft = _draft()
    draft.commercial_constraints.update(
        {
            "target_unit_price": "70000",
            "price_type": "approximate",
        }
    )
    product = _product("SOFT-TARGET", specifications={
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    })
    response = run(
        ProductMatchingService(
            catalogue_service=_StaticCatalogue([product]),
            explanation_provider=_DeterministicExplanation(),
        ).match,
        draft,
    )

    assert response.exact_match_count == 1
    target_preference = next(
        result
        for result in response.products[0].preference_results
        if result.attribute == "target_unit_price"
    )
    assert target_preference.state == "PASS"
    assert "not a hard limit" in target_preference.reason


def test_total_budget_is_checked_against_catalogue_price_times_quantity():
    draft = _draft()
    draft.commercial_constraints.update(
        {
            "total_budget": "150000",
            "quantity": "2",
            "price_type": "total_budget",
        }
    )
    product = _product("OVER-TOTAL-BUDGET", specifications={
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    })
    response = run(
        ProductMatchingService(
            catalogue_service=_StaticCatalogue([product]),
            explanation_provider=_DeterministicExplanation(),
        ).match,
        draft,
    )

    assert response.exact_match_count == 0
    assert response.products == []
    total_result = next(
        result
        for result in _evaluate_structured_prices(product, draft)[0]
        if result.attribute == "total_budget"
    )
    assert total_result.state == "FAIL"
    assert "159998" in total_result.evidence[-1]


def test_live_api_shaped_xps_records_are_matched_by_specifications_not_ids():
    specs = {
        "ram": "16GB",
        "chip": "Intel Core i7",
        "model": "XPS 13",
        "display": "13.4-inch FHD+",
        "storage": "512GB SSD",
        "currency": "INR",
        "processor": "Intel Core i7",
        "display_size": "13.4 inches",
        "display_type": "FHD+",
    }

    def seeded_product(product_id: str, **overrides) -> CatalogueProduct:
        record = {
            "productId": product_id,
            "name": "Dell XPS 13 Laptop",
            "brand": "Dell",
            "category": "Laptops",
            "description": (
                "Compact laptop with Intel Core i7, 16GB RAM, 512GB SSD and "
                "13.4-inch FHD+ display."
            ),
            "price": 89999,
            "discountPrice": 79999,
            "images": ["https://catalogue.example/xps.jpg"],
            "specifications": specs,
            "stock": 10,
            "deliveryDays": 3,
            "rating": 4.7,
            "reviews": 2245,
        }
        record.update(overrides)
        return CatalogueProduct.model_validate(record)

    products = [
        seeded_product("LAPTOP-101"),
        seeded_product(
            "LAPTOP-102",
            name="Dell XPS 13 Plus Laptop",
            price=94999,
            stock=6,
        ),
        seeded_product(
            "LAPTOP-109",
            name="Dell XPS 13 Premium Edition",
            price=119999,
            discountPrice=89999,
            stock=5,
        ),
        seeded_product("LAPTOP-110", name="Dell XPS 13 Business Laptop", stock=0),
        seeded_product(
            "LAPTOP-107",
            name="Dell XPS 13 Laptop 8GB",
            description="Dell XPS 13 with Intel Core i7, 8GB RAM, 512GB SSD and 13.4-inch FHD+ display.",
            specifications={**specs, "ram": "8GB"},
            stock=11,
        ),
    ]
    response = run(
        ProductMatchingService(
            catalogue_service=_StaticCatalogue(products),
            explanation_provider=_DeterministicExplanation(),
        ).match,
        _draft(),
    )

    assert {product.product_id for product in response.products} == {
        "LAPTOP-101",
        "LAPTOP-102",
    }
    assert {
        product.product_id: product.currency
        for product in response.products
    } == {"LAPTOP-101": "INR", "LAPTOP-102": "INR"}
    assert response.products[0].images == ["https://catalogue.example/xps.jpg"]
    excluded = {product.product_id: product for product in response.excluded_products}
    assert {"LAPTOP-109", "LAPTOP-110", "LAPTOP-107"} <= excluded.keys()
    assert any(
        item.attribute == "max_target_unit_price" and item.state == "FAIL"
        for item in excluded["LAPTOP-109"].mandatory_results
    )
    assert any(
        item.attribute == "stock" and item.state == "FAIL"
        for item in excluded["LAPTOP-110"].mandatory_results
    )
    assert any(
        item.attribute == "ram" and item.state == "FAIL"
        for item in excluded["LAPTOP-107"].mandatory_results
    )


def test_catalogue_text_supports_common_specification_aliases_and_keeps_evidence():
    from app.services.product_matching_service import _evaluate_attribute

    product = CatalogueProduct.model_validate(
        {
            "productId": "ALIASES",
            "name": "Dell XPS 13 Laptop",
            "brand": "Dell",
            "description": (
                "Intel Core i7, 16 GB memory, 512 GB solid-state drive, "
                "13.4-inch Full HD Plus display."
            ),
            "specifications": {
                "chip": "Intel Core i7",
                "ram": "16 GB",
                "storage": "512 GB solid-state drive",
                "display": "13.4-inch Full HD Plus",
                "display_size": "13.4 inches",
                "display_type": "Full HD Plus",
                "currency": "INR",
            },
        }
    )

    requirements = {
        "processor": "Intel Core i7",
        "ram": "16GB",
        "storage": "512GB SSD",
        "display_size": "13.4 inches",
        "display_type": "FHD+",
    }
    for attribute, requirement in requirements.items():
        result = _evaluate_attribute(
            product,
            attribute,
            requirement,
            "mandatory_requirements",
        )
        assert result.state == "PASS", (attribute, result.reason)
        assert result.evidence
        assert all(
            "description:" in evidence or "specifications." in evidence
            for evidence in result.evidence
        )


def test_vague_intel_description_does_not_confirm_a_processor_family():
    from app.services.product_matching_service import _evaluate_attribute

    product = _product(
        "VAGUE-CPU",
        description="A laptop with fast Intel performance.",
    )
    result = _evaluate_attribute(
        product,
        "processor",
        "Intel Core i7",
        "mandatory_requirements",
    )

    assert result.state == "UNKNOWN"


def test_display_resolution_plus_sign_is_not_lost_during_matching():
    from app.services.product_matching_service import _evaluate_attribute

    product = _product(
        "DISPLAY-CHECK",
        specifications={"display type": "FHD"},
    )
    result = _evaluate_attribute(product, "display_type", "FHD+", "mandatory_requirements")

    assert result.state == "FAIL"


def test_bayesian_rating_and_review_confidence_rank_many_reviews_over_tiny_sample():
    specs = {
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    }
    popular = _product(
        "POPULAR",
        specifications=specs,
    )
    popular.rating = 4.7
    popular.reviews = 2000
    tiny_sample = _product(
        "TINY-SAMPLE",
        specifications=specs,
    )
    tiny_sample.rating = 5.0
    tiny_sample.reviews = 2
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue([tiny_sample, popular]),
        explanation_provider=_DeterministicExplanation(),
    )

    response = run(service.match, _draft())

    assert response.best_product_id == "POPULAR"
    assert response.products[0].rating == 4.7
    assert response.products[0].reviews == 2000


def test_no_exact_matches_use_rating_and_review_confidence_to_rank_alternatives():
    popular = _product(
        "POPULAR-ALTERNATIVE",
        specifications={"processor": "Intel Core i7"},
    )
    popular.rating = 4.7
    popular.reviews = 2000
    lower_rated = _product(
        "LOWER-RATED-ALTERNATIVE",
        specifications={"processor": "Intel Core i7"},
    )
    lower_rated.rating = 3.8
    lower_rated.reviews = 2000
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue([lower_rated, popular]),
        explanation_provider=_DeterministicExplanation(),
    )

    response = run(service.match, _draft())

    assert response.exact_match_count == 0
    assert [
        product.product_id for product in response.near_matches
    ] == ["POPULAR-ALTERNATIVE", "LOWER-RATED-ALTERNATIVE"]


def test_missing_catalogue_price_currency_cannot_verify_currency_bound_budget():
    specs = {
        "processor": "Intel Core i7-1360P",
        "RAM": "16GB",
        "storage": "512GB SSD",
        "display size": "13.4 inches",
        "display type": "FHD+",
    }
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue(
            [_product("NO-CURRENCY", currency=None, specifications=specs)]
        ),
        explanation_provider=_DeterministicExplanation(),
    )

    response = run(service.match, _draft())

    assert response.exact_match_count == 0
    excluded = response.excluded_products[0]
    budget = next(
        item
        for item in excluded.mandatory_results
        if item.attribute == "max_target_unit_price"
    )
    assert budget.state == "UNKNOWN"
    assert response.near_matches == []


def test_unknown_catalogue_specifications_are_not_classified_as_exact():
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue([_product("UNKNOWN-SPECS")]),
        explanation_provider=_DeterministicExplanation(),
    )

    response = run(service.match, _draft())

    assert response.exact_match_count == 0
    assert response.best_product_id is None
    assert response.status == "no_exact_matches"
    assert [product.product_id for product in response.near_matches] == ["UNKNOWN-SPECS"]
    assert all(
        item.state != "PASS"
        for item in response.near_matches[0].mandatory_results
        if item.attribute in {"processor", "ram", "storage", "display_size", "display_type"}
    )


def test_products_failing_mandatory_requirements_are_excluded_not_near_matches():
    product = _product(
        "WRONG-CONFIG",
        specifications={
            "processor": "Intel Core i5-1235U",
            "RAM": "8GB",
            "storage": "512GB SSD",
            "display size": "13.4 inches",
            "display type": "FHD+",
        },
    )
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue([product]),
        explanation_provider=_DeterministicExplanation(),
    )

    response = run(service.match, _draft())

    assert response.exact_match_count == 0
    assert response.near_match_count == 0
    assert response.products == []
    assert [item.product_id for item in response.excluded_products] == ["WRONG-CONFIG"]
    assert {item.attribute for item in response.excluded_products[0].mandatory_results if item.state == "FAIL"} >= {
        "processor",
        "ram",
    }


def _scarf_draft(quantity: str = "1") -> RequirementDraft:
    return RequirementDraft(
        product_name="Red scarf",
        product_type="Scarf",
        category="Clothing & Accessories",
        mandatory_requirements={"color": "red"},
        commercial_constraints={"quantity": quantity, "unit": "piece"},
    )


def _scarf_product(
    product_id: str,
    *,
    name: str = "Red scarf",
    category: str = "Clothing & Accessories",
    stock: int | None = 10,
    color: str | None = "red",
) -> CatalogueProduct:
    return CatalogueProduct(
        productId=product_id,
        name=name,
        category=category,
        description="A woven winter accessory.",
        price=20,
        currency="USD",
        specifications={"color": color} if color else {},
        stock=stock,
        rating=4.8,
        reviews=500,
    )


def _run_scarf_match(
    products: list[CatalogueProduct],
    draft: RequirementDraft | None = None,
) -> ProductMatchingResponse:
    service = ProductMatchingService(
        catalogue_service=_StaticCatalogue(products),
        explanation_provider=_DeterministicExplanation(),
    )
    return run(service.match, draft or _scarf_draft())


@pytest.mark.parametrize(
    "product",
    [
        _scarf_product(
            "CAST-IRON",
            name="Lodge Cast Iron Cook-It-All Kit",
            category="Kitchen & Dining",
        ),
        _scarf_product(
            "MARINE-FLAG",
            name="Marine distress flags",
            category="Sports & Outdoors",
        ),
        _scarf_product(
            "WIRELESS-EARBUDS",
            name="Wireless earbuds",
            category="Electronics",
        ),
    ],
)
def test_unrelated_product_types_are_excluded_from_scarf_results(product):
    response = _run_scarf_match([product])

    assert response.products == []
    assert response.near_matches == []
    assert [item.product_id for item in response.excluded_products] == [
        product.product_id
    ]


def test_blue_scarf_is_a_relevant_alternative_with_color_difference():
    response = _run_scarf_match(
        [_scarf_product("BLUE-SCARF", name="Blue scarf", color="blue")]
    )

    assert response.exact_match_count == 0
    assert [item.product_id for item in response.near_matches] == ["BLUE-SCARF"]
    color = next(
        item
        for item in response.near_matches[0].mandatory_results
        if item.attribute == "color"
    )
    assert color.state == "FAIL"


def test_unknown_scarf_color_is_an_unverified_relevant_alternative():
    response = _run_scarf_match(
        [_scarf_product("UNKNOWN-COLOR", name="Scarf", color=None)]
    )

    assert response.exact_match_count == 0
    assert [item.product_id for item in response.near_matches] == ["UNKNOWN-COLOR"]
    assert [item.product_id for item in response.unverified_products] == [
        "UNKNOWN-COLOR"
    ]
    color = next(
        item
        for item in response.near_matches[0].mandatory_results
        if item.attribute == "color"
    )
    assert color.state == "UNKNOWN"


def test_stock_one_does_not_make_an_unrelated_product_relevant():
    response = _run_scarf_match(
        [
            _scarf_product(
                "COOKWARE-STOCK-ONE",
                name="Cast iron cookware",
                category="Kitchen & Dining",
                stock=1,
            )
        ]
    )

    assert response.near_matches == []
    assert response.excluded_products[0].product_id == "COOKWARE-STOCK-ONE"


def test_relevant_scarf_with_requested_single_unit_in_stock_is_purchasable():
    response = _run_scarf_match(
        [_scarf_product("ONE-SCARF-IN-STOCK", stock=1)]
    )

    assert [item.product_id for item in response.products] == [
        "ONE-SCARF-IN-STOCK"
    ]


def test_high_stock_does_not_overcome_product_identity_mismatch():
    response = _run_scarf_match(
        [
            _scarf_product(
                "COOKWARE-HIGH-STOCK",
                name="Cast iron cookware",
                category="Kitchen & Dining",
                stock=100,
            )
        ]
    )

    assert response.near_matches == []
    assert response.excluded_products[0].product_id == "COOKWARE-HIGH-STOCK"


def test_requested_quantity_must_be_available_before_product_is_recommended():
    response = _run_scarf_match(
        [
            _scarf_product("TWO-IN-STOCK", stock=2),
            _scarf_product("FIVE-IN-STOCK", stock=5),
        ],
        _scarf_draft(quantity="5"),
    )

    assert [item.product_id for item in response.products] == ["FIVE-IN-STOCK"]
    assert [item.product_id for item in response.near_matches] == []
    insufficient = next(
        item
        for item in response.excluded_products
        if item.product_id == "TWO-IN-STOCK"
    )
    stock = next(
        item for item in insufficient.mandatory_results if item.attribute == "stock"
    )
    assert stock.state == "FAIL"


@pytest.mark.parametrize("unit", ["kilograms", "boxes"])
def test_catalogue_stock_does_not_assume_unverified_requested_units(unit):
    draft = _scarf_draft(quantity="5")
    draft.commercial_constraints["unit"] = unit

    response = _run_scarf_match(
        [_scarf_product("UNIT-UNKNOWN", stock=100)],
        draft,
    )

    assert response.exact_match_count == 0
    assert response.near_matches == []
    stock = next(
        item
        for item in response.excluded_products[0].mandatory_results
        if item.attribute == "stock"
    )
    assert stock.state == "UNKNOWN"


def test_related_shawl_is_an_alternative_only_with_relevant_category():
    response = _run_scarf_match(
        [
            _scarf_product(
                "RELATED-SHAWL",
                name="Red shawl",
                color="red",
            )
        ]
    )

    assert response.products == []
    assert [item.product_id for item in response.near_matches] == ["RELATED-SHAWL"]


def test_no_relevant_products_returns_no_suitable_match_state():
    response = _run_scarf_match(
        [
            _scarf_product(
                "UNRELATED-1",
                name="Cast iron cookware",
                category="Kitchen & Dining",
            ),
            _scarf_product(
                "UNRELATED-2",
                name="Wireless earbuds",
                category="Electronics",
            ),
        ]
    )

    assert response.status == "no_relevant_products"
    assert response.products == []
    assert response.near_matches == []


def test_existing_product_match_route_accepts_requirement_draft(monkeypatch):
    async def fake_match(draft: RequirementDraft) -> ProductMatchingResponse:
        assert draft.product_name == "Dell XPS 13 laptop"
        assert draft.commercial_constraints["quantity"] == "1"
        return ProductMatchingResponse(
            status="no_exact_matches",
            catalogue_products_examined=1,
            catalogue_products_returned=1,
            catalogue_total_reported=1,
            exact_match_count=0,
            near_match_count=0,
        )

    class _Service:
        match = staticmethod(fake_match)

    monkeypatch.setattr(products_endpoint, "ProductMatchingService", _Service)
    response = TestClient(app).post("/api/products/match", json=_draft().model_dump())

    assert response.status_code == 200
    assert response.json()["status"] == "no_exact_matches"


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_detail"),
    [
        (
            CatalogueUnavailable,
            503,
            "The product catalogue is temporarily unavailable. Please try again shortly.",
        ),
        (
            CatalogueTimeout,
            504,
            "The product catalogue is taking longer than expected. Please try again shortly.",
        ),
        (
            CatalogueInvalidResponse,
            502,
            "The product catalogue returned an invalid response. Please try again later.",
        ),
    ],
)
def test_product_match_route_returns_clear_catalogue_errors(
    monkeypatch,
    failure,
    expected_status: int,
    expected_detail: str,
):
    async def fail_match(_: RequirementDraft) -> ProductMatchingResponse:
        raise failure("internal upstream detail")

    class _Service:
        match = staticmethod(fail_match)

    monkeypatch.setattr(products_endpoint, "ProductMatchingService", _Service)
    response = TestClient(app).post("/api/products/match", json=_draft().model_dump())

    assert response.status_code == expected_status
    assert response.json()["error"] == expected_detail
    assert "internal upstream detail" not in response.text


def test_existing_product_match_route_keeps_legacy_demand_request_dispatch(monkeypatch):
    payload = ProductMatchRequest(
        query="Custom Industrial Valve",
        category="Industrial Hardware",
        target_price=500,
        target_quantity=2,
    )
    monkeypatch.setattr(
        products_endpoint.ProductService,
        "match_demand_parameters",
        staticmethod(lambda request: {"query": request.query, "quantity": request.target_quantity}),
    )

    result = run(products_endpoint.match_product_specifications, payload)

    assert result == {"query": "Custom Industrial Valve", "quantity": 2}
