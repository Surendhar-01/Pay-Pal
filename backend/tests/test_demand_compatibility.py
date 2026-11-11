from app.services.demand_compatibility import (
    SemanticCandidateEdge,
    assess_group_compatibility,
    assess_demand_grouping_readiness,
    build_compatible_clusters,
)
from app.services.demand_normalization import normalize_demand_v2


def _demand(
    name,
    category,
    mandatory=None,
    optional=None,
    minimum=100,
    maximum=200,
    currency="USD",
    brand=None,
):
    return {
        "id": name,
        "title": name,
        "brand": brand,
        "category": category,
        "normalized_requirements": {
            "product_type": name,
            "normalized_product_name": name,
            "normalized_category": category,
            "mandatory_requirements": mandatory or [],
            "optional_preferences": optional or [],
            "commercial_constraints": {
                "minimum_target_unit_price": minimum,
                "maximum_target_unit_price": maximum,
                "currency": currency,
            },
        },
    }


def _spec(name, value):
    return {"name": name, "value": value}


def test_display_size_values_are_parsed_as_inches():
    for value in ("13.4 inches", "14 inch", "15.6 inches", "14 in", 13.4):
        demand = _demand("reliable laptop", "Computers", mandatory=[_spec("display_size", value)])

        assessment = assess_demand_grouping_readiness(demand)

        assert assessment.status == "compatible", (value, assessment)


def test_display_resolutions_are_recognized_and_aliases_are_preserved():
    for value in ("FHD+", "FHD", "QHD", "UHD", "4K"):
        demand = _demand("reliable laptop", "Computers", mandatory=[_spec("display_type", value)])

        assessment = assess_demand_grouping_readiness(demand)

        assert assessment.status == "compatible", (value, assessment)

    source = _demand("reliable laptop", "Computers", mandatory=[_spec("display_type", "FHD+")])
    equivalent_name = _demand("business laptop", "Computers", mandatory=[_spec("display_technology", "FHD+")])
    original_name = source["normalized_requirements"]["mandatory_requirements"][0]["name"]

    assert assess_group_compatibility([source, equivalent_name]).status == "compatible"
    assert source["normalized_requirements"]["mandatory_requirements"][0]["name"] == original_name == "display_type"

    resolution = _demand("reliable laptop", "Computers", mandatory=[_spec("display_type", "FHD+")])
    panel_technology = _demand("business laptop", "Computers", mandatory=[_spec("display_technology", "AMOLED")])
    assert assess_group_compatibility([resolution, panel_technology]).status == "compatible"


def test_unsupported_display_value_remains_unresolved():
    demand = _demand("reliable laptop", "Computers", mandatory=[_spec("display_type", "Retina XDR")])

    assessment = assess_demand_grouping_readiness(demand)

    assert assessment.status == "needs_review"
    assert "mandatory_value_unparsed:display_technology" in assessment.reasons


def test_incompatible_mandatory_display_sizes_and_resolutions_are_rejected():
    smaller = _demand("reliable laptop", "Computers", mandatory=[_spec("display_size", "13.4 inches")])
    larger = _demand("business laptop", "Computers", mandatory=[_spec("display_size", "14 inch")])
    size_assessment = assess_group_compatibility([smaller, larger])

    assert size_assessment.status == "rejected"
    assert "mandatory_intersection_empty:screen_size" in size_assessment.reasons

    full_hd = _demand("reliable laptop", "Computers", mandatory=[_spec("display_type", "FHD")])
    quad_hd = _demand("business laptop", "Computers", mandatory=[_spec("display_technology", "QHD")])
    resolution_assessment = assess_group_compatibility([full_hd, quad_hd])

    assert resolution_assessment.status == "rejected"
    assert "mandatory_intersection_empty:display_resolution" in resolution_assessment.reasons


def test_hp_laptop_normalized_display_requirements_pass_readiness():
    demand = _demand("hp laptop", "Computers", mandatory=[
        _spec("processor", "Intel Core i7 (12th generation or newer)"),
        _spec("ram", "24GB"),
        _spec("storage", "512GB SSD"),
        _spec("display_size", "13.4 inches"),
        _spec("display_type", "FHD+"),
    ])
    normalized_source = demand["normalized_requirements"]
    normalized_source.update({
        "status": "Completed",
        "normalized_specifications": {
            "ram": "24GB",
            "storage": "512GB SSD",
            "processor": "Intel Core i7 (12th generation or newer)",
            "display_size": "13.4 inches",
            "display_type": "FHD+",
        },
        "optional_preferences": [
            _spec("application", "Professional use"),
            _spec("recommendation_style", "Similar alternatives"),
        ],
        "commercial_constraints": {
            "unit": "unit",
            "currency": "INR",
            "quantity": "1",
            "maximum_target_unit_price": "70000",
            "minimum_target_unit_price": "65000",
        },
        "delivery_constraints": {
            "expected_lead_time": 8,
            "additional_delivery_requirements": "price_type: maximum; max_target_unit_price: 70000",
        },
    })

    normalized = normalize_demand_v2(demand, normalized_source)
    assessment = assess_demand_grouping_readiness({
        **demand,
        "normalized_requirements": normalized,
    })

    assert [(item["name"], item["value"]) for item in normalized["mandatory_requirements"][-2:]] == [
        ("display_size", "13.4 inches"),
        ("display_type", "FHD+"),
    ]
    assert assessment.status == "compatible"
    assert assessment.reasons == ()


def test_laptop_pair_uses_persisted_schema_and_normalizes_capacity_units():
    first = _demand(
        "reliable laptop",
        "Computers",
        mandatory=[
            _spec("RAM", "minimum 16 GB"),
            _spec("SSD capacity", "at least 512 GB SSD"),
            _spec("processor", "Intel Core i5 13th-generation or newer"),
        ],
    )
    second = _demand(
        "laptop suitable",
        "Electronics > Computers > Laptops",
        mandatory=[
            _spec("memory", "at least 16384 MiB"),
            _spec("storage", "minimum 0.5 TB SSD"),
            _spec("CPU", "Intel Core i5 14th-generation or newer"),
        ],
    )

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"
    assert assessment.reasons == ()


def test_samsung_smartphone_pair_is_compatible():
    first = _demand(
        "Samsung 5G smartphone",
        "Electronics > Mobile Phones",
        mandatory=[_spec("network", "5G")],
    )
    second = _demand(
        "Samsung Galaxy smartphone suitable",
        "Mobile Phones",
        mandatory=[_spec("network", "5G")],
    )

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"


def test_over_ear_headphones_pair_is_compatible_and_optional_preferences_only_rank():
    first = _demand(
        "affordable wireless over-ear headphones",
        "Electronics > Audio > Headphones",
        mandatory=[_spec("battery life", "at least 30 hours")],
        optional=[_spec("color", "black")],
    )
    second = _demand(
        "Bluetooth over-ear headphones mainly",
        "Audio > Headphones",
        mandatory=[_spec("battery life", "at least 40 hours")],
        optional=[_spec("color", "white")],
    )

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"
    assert assessment.optional_preference_score == 0


def test_headphone_feature_aliases_normalize_before_strict_intersection():
    first = _demand(
        "wireless over-ear headphones",
        "Electronics > Audio > Wireless Headphones",
        mandatory=[
            _spec("good_bass", "required"),
            _spec("bluetooth_5_2_or_newer", "required"),
            _spec("usb_type_c_charging", "required"),
            _spec("active_noise_cancellation", "required"),
            _spec("battery_life", "at least 30 hours"),
        ],
    )
    second = _demand(
        "wireless headphones",
        "Audio > Wireless Headphones",
        mandatory=[
            _spec("bass_response", "good bass"),
            _spec("bluetooth_version", "Bluetooth 5.2 or newer"),
            _spec("charging_port", "USB Type-C"),
            _spec("noise_cancellation", "Active Noise Cancellation"),
            _spec("battery life", "at least 40 hours"),
        ],
    )

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"
    assert assessment.reasons == ()


def test_generic_noise_cancellation_required_marker_means_active_anc():
    first = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("noise_cancellation", "required")],
    )
    second = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("active_noise_cancellation", "Active Noise Cancellation")],
    )

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"


def test_incomplete_mandatory_requirement_requires_review_with_evidence():
    first = _demand("wireless headphones", "Headphones")
    second = _demand("over-ear headphones", "Headphones")
    first["normalized_requirements"]["mandatory_requirements"] = [{"name": "Bluetooth version"}]

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "mandatory_requirement_incomplete" in assessment.reasons
    assert any("Bluetooth version" in item for item in assessment.evidence)


def test_identical_headphone_requirements_are_compatible():
    requirements = [
        _spec("noise_cancellation", "Active Noise Cancellation"),
        _spec("bluetooth_version", "Bluetooth 5.2 or newer"),
        _spec("battery_life", "at least 30 hours"),
        _spec("microphone", "clear built-in microphone"),
        _spec("charging_port", "USB Type-C"),
    ]
    first = _demand("wireless over-ear headphones", "Electronics > Audio > Headphones", mandatory=requirements)
    second = _demand("wireless over-ear headphones", "Audio > Headphones", mandatory=requirements)

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"


def test_microphone_and_clear_built_in_microphone_aliases_are_equivalent():
    first = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("microphone", "clear built-in microphone")],
    )
    second = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("clear_built_in_microphone", "required")],
    )

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"


def test_microphone_quality_text_aliases_are_equivalent():
    first = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("microphone quality", "clear built-in microphone")],
    )
    second = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("microphone quality", "clear microphone")],
    )

    assert assess_group_compatibility([first, second]).status == "compatible"


def test_microphone_presence_requirement_is_satisfied_by_clear_built_in_microphone():
    presence = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("microphone", "required")],
    )
    clear_microphone = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("clear_built_in_microphone", "required")],
    )

    assert assess_group_compatibility([presence, clear_microphone]).status == "compatible"


def test_identical_clear_microphone_quality_requirements_are_compatible():
    first = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("clear_built_in_microphone", "required")],
    )
    second = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("microphone quality", "clear built-in microphone")],
    )

    assert assess_group_compatibility([first, second]).status == "compatible"


def test_conflicting_microphone_quality_requirements_are_rejected_with_evidence():
    clear = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("microphone quality", "clear microphone audio")],
    )
    muffled = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("microphone quality", "muffled microphone audio")],
    )

    assessment = assess_group_compatibility([clear, muffled])

    assert assessment.status == "rejected"
    assert "mandatory_value_conflict:microphone" in assessment.reasons
    assert any("clear microphone audio" in item and "muffled microphone audio" in item for item in assessment.evidence)


def test_required_feature_marker_does_not_conflict_with_explicit_not_required():
    required = _demand(
        "wireless over-ear headphones", "Headphones",
        mandatory=[_spec("active_noise_cancellation", "required")],
    )
    not_required = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("noise_cancellation", "not required")],
    )

    assessment = assess_group_compatibility([required, not_required])

    assert assessment.status == "compatible"


def test_bluetooth_minimum_conflicts_with_a_lower_exact_version():
    minimum = _demand(
        "wireless headphones", "Headphones",
        mandatory=[_spec("bluetooth_version", "Bluetooth 5.2 or newer")],
    )
    exact = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("bluetooth_version", "Bluetooth 5.0")],
    )

    assessment = assess_group_compatibility([minimum, exact])

    assert assessment.classification == "incompatible"
    assert "mandatory_intersection_empty:bluetooth_version" in assessment.reasons
    assert any("Bluetooth 5.2" in item and "Bluetooth 5.0" in item for item in assessment.evidence)


def test_battery_minimums_and_shared_unit_ranges_are_compatible():
    thirty = _demand(
        "wireless headphones", "Headphones",
        mandatory=[_spec("battery_life", "at least 30 hours")],
    )
    thirty_five = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("battery life", "at least 35 hours")],
    )
    ranged = _demand(
        "Bluetooth headphones", "Headphones",
        mandatory=[_spec("battery life", "between 30 and 35 hours")],
    )

    assert assess_group_compatibility([thirty, thirty_five]).status == "compatible"
    assert assess_group_compatibility([thirty, ranged]).status == "compatible"


def test_conflicting_battery_minimum_and_maximum_are_rejected_with_evidence():
    minimum = _demand(
        "wireless headphones", "Headphones",
        mandatory=[_spec("battery life", "at least 35 hours")],
    )
    maximum = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("battery_life", "up to 30 hours")],
    )

    assessment = assess_group_compatibility([minimum, maximum])

    assert assessment.classification == "incompatible"
    assert "mandatory_intersection_empty:battery_life" in assessment.reasons
    assert any("35 hours" in item and "30 hours" in item for item in assessment.evidence)


def test_usb_type_c_alias_and_descriptive_value_are_consistent():
    described = _demand(
        "wireless headphones", "Headphones",
        mandatory=[_spec("charging_port", "USB Type-C")],
    )
    marker = _demand(
        "over-ear headphones", "Headphones",
        mandatory=[_spec("usb_type_c_charging", "required")],
    )

    assert assess_group_compatibility([described, marker]).status == "compatible"


def test_overlapping_budget_ranges_are_compatible():
    first = _demand("reliable laptop", "Computers", minimum=100, maximum=250)
    second = _demand("business laptop", "Computers", minimum=200, maximum=300)

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"


def test_one_missing_budget_requires_review():
    budgeted = _demand("reliable laptop", "Computers", minimum=100, maximum=250)
    unbudgeted = _demand("business laptop", "Computers", minimum=None, maximum=None)

    assessment = assess_group_compatibility([budgeted, unbudgeted])

    assert assessment.status == "needs_review"
    assert "budget_missing" in assessment.reasons
    assert any("budget information is incomplete" in item for item in assessment.evidence)


def test_missing_normalized_mandatory_information_requires_review():
    complete = _demand(
        "reliable laptop", "Computers",
        mandatory=[_spec("RAM", "at least 16 GB")],
    )
    incomplete = _demand("laptop suitable", "Computers")
    incomplete["normalized_requirements"].update({
        "status": "Needs Clarification",
        "missing_information": ["mandatory RAM requirement"],
    })

    assessment = assess_group_compatibility([complete, incomplete])

    assert assessment.status == "needs_review"
    assert "mandatory_information_missing" in assessment.reasons
    assert any("mandatory RAM requirement" in item for item in assessment.evidence)


def test_different_requested_quantities_with_equivalent_units_remain_compatible():
    first = _demand("reliable laptop", "Computers")
    second = _demand("business laptop", "Computers")
    first["normalized_requirements"]["commercial_constraints"].update({"quantity": 2, "unit": "pieces"})
    second["normalized_requirements"]["commercial_constraints"].update({"quantity": 20, "unit": "units"})

    assert assess_group_compatibility([first, second]).status == "compatible"


def test_verified_quantity_unit_conversion_does_not_change_aggregation():
    first = _demand("reliable laptop", "Computers")
    second = _demand("business laptop", "Computers")
    first["normalized_requirements"]["commercial_constraints"].update({"quantity": 2, "unit": "kg"})
    second["normalized_requirements"]["commercial_constraints"].update({"quantity": 500, "unit": "g"})

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "compatible"
    assert assessment.reasons == ()


def test_quantity_units_from_incompatible_dimensions_are_rejected():
    mass = _demand("bulk rice", "Food")
    volume = _demand("rice", "Food")
    mass["normalized_requirements"]["commercial_constraints"].update({"quantity": 2, "unit": "kg"})
    volume["normalized_requirements"]["commercial_constraints"].update({"quantity": 2, "unit": "l"})

    assessment = assess_group_compatibility([mass, volume])

    assert assessment.status == "rejected"
    assert "quantity_unit_incompatible" in assessment.reasons


def test_nonpositive_requested_quantity_requires_review():
    first = _demand("reliable laptop", "Computers")
    second = _demand("business laptop", "Computers")
    first["normalized_requirements"]["commercial_constraints"].update({"quantity": 0, "unit": "units"})
    second["normalized_requirements"]["commercial_constraints"].update({"quantity": 2, "unit": "units"})

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "quantity_invalid" in assessment.reasons
    assert any("0" in item for item in assessment.evidence)


def test_identical_production_shaped_headphone_requirements_are_compatible_after_normalization():
    mandatory = [
        _spec("noise_cancellation", "Active Noise Cancellation"),
        _spec("bluetooth_version", "Bluetooth 5.2 or newer"),
        _spec("battery_life", "at least 30 hours"),
        _spec("microphone", "clear built-in microphone"),
        _spec("charging_port", "USB Type-C"),
        _spec("active_noise_cancellation", "required"),
        _spec("bluetooth_5_2_or_newer", "required"),
        _spec("clear_built_in_microphone", "required"),
        _spec("usb_type_c_charging", "required"),
        _spec("good_bass", "required"),
    ]
    records = []
    for demand_id in ("headphone-a", "headphone-b", "headphone-c"):
        record = _demand(
            "wireless over-ear headphones",
            "Electronics > Audio > Wireless Headphones",
            mandatory=mandatory,
            optional=[_spec("multipoint_connectivity", "preferred")],
            minimum=2500,
            maximum=6000,
            currency="INR",
        )
        record["id"] = demand_id
        record["normalized_requirements"] = normalize_demand_v2(
            record,
            record["normalized_requirements"],
        )
        records.append(record)

    assessment = assess_group_compatibility(records)

    assert assessment.status == "compatible"
    assert assessment.reasons == ()


def test_headphones_with_disjoint_budgets_are_rejected():
    first = _demand("wireless over-ear headphones", "Headphones", minimum=100, maximum=150)
    second = _demand("Bluetooth over-ear headphones", "Headphones", minimum=151, maximum=220)

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "rejected"
    assert "budget_intersection_empty" in assessment.reasons


def test_incompatible_numeric_mandatory_specs_are_rejected():
    first = _demand("reliable laptop", "Computers", mandatory=[_spec("RAM", "at least 16GB")])
    second = _demand("laptop suitable", "Computers", mandatory=[_spec("memory", "at most 8 GB")])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "rejected"
    assert "mandatory_intersection_empty:ram" in assessment.reasons


def test_cross_category_or_product_family_is_rejected():
    laptop = _demand("reliable laptop", "Computers")
    phone = _demand("Samsung smartphone", "Mobile Phones")

    assessment = assess_group_compatibility([laptop, phone])

    assert assessment.status == "rejected"
    assert "category_mismatch" in assessment.reasons
    assert "product_identity_mismatch" in assessment.reasons


def test_missing_mandatory_requirements_are_neutral():
    specified = _demand("reliable laptop", "Computers", mandatory=[_spec("RAM", "minimum 16GB")])
    unspecified = _demand("laptop suitable", "Computers", mandatory=[])

    assessment = assess_group_compatibility([specified, unspecified])

    assert assessment.status == "compatible"


def test_unsupported_numeric_format_returns_needs_review():
    first = _demand("reliable laptop", "Computers", mandatory=[_spec("RAM", "16 modules")])
    second = _demand("laptop suitable", "Computers", mandatory=[_spec("RAM", "32 modules")])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "mandatory_numeric_unparsed:ram" in assessment.reasons


def test_group_budget_and_spec_intersections_cover_all_members():
    first = _demand(
        "reliable laptop", "Computers",
        mandatory=[_spec("RAM", "between 8GB and 16GB")],
    )
    second = _demand(
        "laptop suitable", "Computers",
        mandatory=[_spec("RAM", "between 12GB and 20GB")],
    )
    third = _demand(
        "business laptop", "Computers",
        mandatory=[_spec("RAM", "between 18GB and 32GB")],
    )

    assert assess_group_compatibility([first, second]).status == "compatible"
    assert assess_group_compatibility([second, third]).status == "compatible"
    whole_group = assess_group_compatibility([first, second, third])
    assert whole_group.status == "rejected"
    assert "mandatory_intersection_empty:ram" in whole_group.reasons

    plan = build_compatible_clusters(
        {"a": first, "b": second, "c": third},
        [
            SemanticCandidateEdge("a", "b", 0.1),
            SemanticCandidateEdge("b", "c", 0.2),
        ],
    )
    assert plan.groups == (("a", "b"),)
    assert len(plan.rejected_merges) == 1
    assert "mandatory_intersection_empty:ram" in plan.rejected_merges[0].reasons


def test_budget_intersection_requires_comparable_currency():
    usd = _demand("reliable laptop", "Computers", currency="USD")
    eur = _demand("laptop suitable", "Computers", currency="EUR")

    assessment = assess_group_compatibility([usd, eur])

    assert assessment.status == "needs_review"
    assert "budget_currency_mismatch" in assessment.reasons


def test_currency_mismatch_requires_review_even_without_budget_bounds():
    usd = _demand("reliable laptop", "Computers", minimum=None, maximum=None, currency="USD")
    eur = _demand("business laptop", "Computers", minimum=None, maximum=None, currency="EUR")

    assessment = assess_group_compatibility([usd, eur])

    assert assessment.status == "needs_review"
    assert "budget_currency_mismatch" in assessment.reasons
    assert any("EUR" in item and "USD" in item for item in assessment.evidence)


def test_battery_capacity_accepts_thousands_separators_and_lower_bound_forms():
    demands = [
        _demand("Samsung smartphone", "Mobile Phones", mandatory=[
            _spec("battery_capacity", "4,500mAh or higher"),
        ]),
        _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
            _spec("battery_capacity", "at least 4,500 mAh"),
        ]),
        _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
            _spec("battery_capacity", "4500 mAh"),
        ]),
    ]

    assert assess_group_compatibility(demands).status == "compatible"


def test_incompatible_battery_capacity_thresholds_are_rejected():
    first = _demand("Samsung smartphone", "Mobile Phones", mandatory=[
        _spec("battery_capacity", "at least 4,500mAh"),
    ])
    second = _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
        _spec("battery_capacity", "up to 4,499 mAh"),
    ])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "rejected"
    assert "mandatory_intersection_empty:battery_capacity" in assessment.reasons


def test_bluetooth_version_prefixes_and_lower_bounds_are_compared():
    first = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.2 or newer"),
    ])
    second = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "5.2 or newer"),
    ])
    third = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "at least 5.3"),
    ])

    assert assess_group_compatibility([first, second]).status == "compatible"
    assert assess_group_compatibility([first, third]).status == "compatible"


def test_bluetooth_minimum_formats_and_exact_versions_are_distinguished():
    exact_52 = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.2"),
    ])
    below_52 = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.1"),
    ])

    for requirement in ("Bluetooth 5.2+", "5.2 or newer", "at least Bluetooth 5.2", ">= 5.2"):
        minimum = _demand("over-ear headphones", "Headphones", mandatory=[
            _spec("bluetooth_version", requirement),
        ])
        assert assess_group_compatibility([minimum, exact_52]).status == "compatible", requirement
        assert assess_group_compatibility([minimum, below_52]).status == "rejected", requirement

    exact_53 = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.3"),
    ])
    assert assess_group_compatibility([exact_52, exact_53]).status == "rejected"


def test_bluetooth_maximum_formats_and_conflicting_intervals_are_compared():
    exact_51 = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.1"),
    ])
    exact_53 = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.3"),
    ])

    for requirement in ("up to 5.2", "at most Bluetooth 5.2", "maximum of 5.2", "<= 5.2"):
        maximum = _demand("over-ear headphones", "Headphones", mandatory=[
            _spec("bluetooth_version", requirement),
        ])
        assert assess_group_compatibility([maximum, exact_51]).status == "compatible", requirement
        assert assess_group_compatibility([maximum, exact_53]).status == "rejected", requirement

    minimum = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.2+"),
    ])
    maximum = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "<= 5.1"),
    ])
    assessment = assess_group_compatibility([minimum, maximum])
    assert assessment.status == "rejected"
    assert "mandatory_intersection_empty:bluetooth_version" in assessment.reasons


def test_bluetooth_or_older_is_an_upper_bound():
    maximum = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.2 or older"),
    ])
    exact_51 = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.1"),
    ])
    exact_53 = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.3"),
    ])

    assert assess_group_compatibility([maximum, exact_51]).status == "compatible"
    assert assess_group_compatibility([maximum, exact_53]).status == "rejected"


def test_unsupported_bluetooth_ranges_require_review_not_exact_interpretation():
    unsupported = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.2 to 5.4"),
    ])
    exact = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "Bluetooth 5.2"),
    ])

    assessment = assess_group_compatibility([unsupported, exact])

    assert assessment.status == "needs_review"
    assert "mandatory_value_unparsed:bluetooth_version" in assessment.reasons


def test_incompatible_bluetooth_version_thresholds_are_rejected():
    first = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "at least Bluetooth 5.2"),
    ])
    second = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("bluetooth_version", "up to 5.1"),
    ])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "rejected"
    assert "mandatory_intersection_empty:bluetooth_version" in assessment.reasons


def test_display_technology_alternatives_require_a_shared_supported_value():
    alternatives = _demand("Samsung smartphone", "Mobile Phones", mandatory=[
        _spec("display_type", "Super AMOLED or AMOLED"),
    ])
    amoled = _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
        _spec("display", "AMOLED display"),
    ])

    assessment = assess_group_compatibility([alternatives, amoled])

    assert assessment.status == "compatible"

    super_amoled_only = _demand("Samsung smartphone", "Mobile Phones", mandatory=[
        _spec("display_type", "Super AMOLED"),
    ])
    plain_amoled_only = _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
        _spec("display", "AMOLED"),
    ])
    incompatible = assess_group_compatibility([super_amoled_only, plain_amoled_only])

    assert incompatible.status == "rejected"
    assert "mandatory_intersection_empty:display_technology" in incompatible.reasons


def test_os_and_windows_support_aliases_compare_as_exact_values():
    first = _demand("reliable laptop", "Computers", mandatory=[
        _spec("os_support", "Windows 11"),
        _spec("windows_11_support", "required"),
    ])
    second = _demand("business laptop", "Computers", mandatory=[
        _spec("os_compatibility", "windows 11"),
        _spec("windows_11_compatibility", "required"),
    ])

    assert assess_group_compatibility([first, second]).status == "compatible"


def test_network_aliases_match_only_the_same_known_technology():
    first = _demand("Samsung smartphone", "Mobile Phones", mandatory=[
        _spec("network_support", "5g"),
    ])
    same = _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
        _spec("connectivity", "5G"),
    ])
    different = _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
        _spec("connectivity", "4G LTE"),
    ])

    assert assess_group_compatibility([first, same]).status == "compatible"
    conflict = assess_group_compatibility([first, different])
    assert conflict.status == "rejected"
    assert "mandatory_value_conflict:network_technology" in conflict.reasons


def test_malformed_battery_capacity_fragments_remain_needs_review():
    first = _demand("Samsung smartphone", "Mobile Phones", mandatory=[
        _spec("capacity", "battery of 4"),
    ])
    second = _demand("Samsung mobile phone", "Mobile Phones", mandatory=[
        _spec("capacity", "battery of at least 4"),
    ])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "mandatory_value_unparsed:capacity" in assessment.reasons


def test_different_free_text_applications_remain_needs_review():
    first = _demand("reliable laptop", "Computers", mandatory=[
        _spec("application", "software development and college projects"),
    ])
    second = _demand("business laptop", "Computers", mandatory=[
        _spec("application", "coding and running development tools"),
    ])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "mandatory_value_unparsed:application" in assessment.reasons


def test_microphone_alias_does_not_equate_different_quality_descriptions():
    first = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("microphone", "clear built-in microphone"),
    ])
    second = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("microphone_quality", "good-quality microphone for clear calls"),
    ])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "mandatory_value_unparsed:microphone" in assessment.reasons


def test_v2_application_tags_use_union_semantics_and_unknowns_need_review():
    first = _demand("reliable laptop", "Computers")
    first["normalized_requirements"].update({
        "mandatory_requirements": [],
        "application_requirements": {
            "mandatory_tags": ["gaming"],
            "intended_use_tags": ["software_development"],
            "preferred_tags": [],
            "unknown_phrases": [],
        },
        "microphone_requirements": {
            "presence_required": False,
            "quality_tags": [],
            "unknown_phrases": [],
        },
    })
    second = _demand("business laptop", "Computers")
    second["normalized_requirements"].update({
        "mandatory_requirements": [],
        "application_requirements": {
            "mandatory_tags": ["social_media"],
            "intended_use_tags": ["education"],
            "preferred_tags": [],
            "unknown_phrases": [],
        },
        "microphone_requirements": {
            "presence_required": False,
            "quality_tags": [],
            "unknown_phrases": [],
        },
    })

    assert assess_group_compatibility([first, second]).status == "compatible"

    second["normalized_requirements"]["application_requirements"]["unknown_phrases"] = [
        "quantum field telemetry",
    ]
    assessment = assess_group_compatibility([first, second])
    assert assessment.status == "needs_review"
    assert "application_use_case_unrecognized" in assessment.reasons


def test_v2_microphone_presence_unions_but_distinct_quality_stays_needs_review():
    first = _demand("wireless headphones", "Headphones")
    first["normalized_requirements"].update({
        "mandatory_requirements": [],
        "microphone_requirements": {
            "presence_required": True,
            "quality_tags": ["clarity"],
            "unknown_phrases": [],
        },
    })
    presence_only = _demand("over-ear headphones", "Headphones")
    presence_only["normalized_requirements"].update({
        "mandatory_requirements": [],
        "microphone_requirements": {
            "presence_required": True,
            "quality_tags": [],
            "unknown_phrases": [],
        },
    })
    other_quality = _demand("Bluetooth headphones", "Headphones")
    other_quality["normalized_requirements"].update({
        "mandatory_requirements": [],
        "microphone_requirements": {
            "presence_required": True,
            "quality_tags": ["good_quality", "clear_calls"],
            "unknown_phrases": [],
        },
    })

    assert assess_group_compatibility([first, presence_only]).status == "compatible"
    assessment = assess_group_compatibility([first, other_quality])
    assert assessment.status == "needs_review"
    assert "microphone_quality_semantics_unresolved" in assessment.reasons


def test_v2_microphone_quality_tag_aliases_are_compatible():
    clarity = _demand("wireless headphones", "Headphones")
    clarity["normalized_requirements"].update({
        "mandatory_requirements": [],
        "microphone_requirements": {
            "presence_required": True,
            "quality_tags": ["clarity"],
            "unknown_phrases": [],
        },
    })
    clear_calls = _demand("over-ear headphones", "Headphones")
    clear_calls["normalized_requirements"].update({
        "mandatory_requirements": [],
        "microphone_requirements": {
            "presence_required": True,
            "quality_tags": ["clear_calls"],
            "unknown_phrases": [],
        },
    })

    assert assess_group_compatibility([clarity, clear_calls]).status == "compatible"


def test_v2_conflicting_battery_sources_need_review():
    first = _demand("Samsung smartphone", "Mobile Phones")
    first["normalized_requirements"].update({
        "battery_capacity_source_conflict": True,
        "mandatory_requirements": [],
    })
    second = _demand("Samsung mobile phone", "Mobile Phones")
    second["normalized_requirements"]["mandatory_requirements"] = []

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "battery_capacity_source_conflict" in assessment.reasons


def test_unknown_identical_mandatory_values_remain_needs_review():
    first = _demand("reliable laptop", "Computers", mandatory=[
        _spec("unrecognized_constraint", "opaque value"),
    ])
    second = _demand("business laptop", "Computers", mandatory=[
        _spec("unrecognized_constraint", "opaque value"),
    ])

    assessment = assess_group_compatibility([first, second])

    assert assessment.status == "needs_review"
    assert "mandatory_value_unparsed:unrecognized_constraint" in assessment.reasons


def test_known_headphone_feature_flags_allow_exact_matching():
    first = _demand("wireless headphones", "Headphones", mandatory=[
        _spec("active_noise_cancellation", "required"),
        _spec("bluetooth_5_2_or_newer", "required"),
        _spec("noise_cancellation", "Active Noise Cancellation"),
        _spec("usb_type_c_charging", "required"),
    ])
    second = _demand("over-ear headphones", "Headphones", mandatory=[
        _spec("active_noise_cancellation", "required"),
        _spec("bluetooth_5_2_or_newer", "required"),
        _spec("noise_cancellation", "Active Noise Cancellation"),
        _spec("usb_type_c_charging", "required"),
    ])

    assert assess_group_compatibility([first, second]).status == "compatible"