import pytest

from app.services.demand_family_taxonomy import canonical_demand_family


@pytest.mark.parametrize(
    ("product_name", "category", "family_key", "category_key"),
    [
        ("HP laptop", "Computers", "laptop", "computer_laptops"),
        ("reliable laptop", "Computers", "laptop", "computer_laptops"),
        ("laptop suitable", "Computers", "laptop", "computer_laptops"),
        ("Samsung 5G smartphone", "Mobile Phones", "samsung_smartphone", "samsung_mobile_phones"),
        (
            "Samsung Galaxy smartphone suitable",
            "Electronics -> Mobile Phones -> Samsung Smartphones",
            "samsung_smartphone",
            "samsung_mobile_phones",
        ),
        ("wireless over-ear headphones", "Electronics", "wireless_headphones", "wireless_headphones"),
        (
            "wireless over-ear headphones",
            "Electronics → Audio → Wireless Headphones",
            "wireless_headphones",
            "wireless_headphones",
        ),
        (
            "affordable wireless over-ear headphones",
            "Electronics -> Audio -> Wireless Headphones",
            "wireless_headphones",
            "wireless_headphones",
        ),
    ],
)
def test_approved_product_category_aliases(product_name, category, family_key, category_key):
    result = canonical_demand_family(product_name, category)

    assert result is not None
    assert result.product_family_key == family_key
    assert result.category_key == category_key


@pytest.mark.parametrize(
    ("product_name", "category"),
    [
        ("business laptop", "Computers"),
        ("Bluetooth speaker", "Electronics"),
        ("Samsung 5G smartphone", "Electronics"),
        ("wireless over-ear headphones", "Electronics > Audio"),
        ("wireless over-ear headphones", "Automotive > Audio Systems"),
    ],
)
def test_unapproved_or_broad_category_combinations_need_review(product_name, category):
    assert canonical_demand_family(product_name, category) is None