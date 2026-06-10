import pandas as pd

from step5_fetch_coordinates import (
    build_final_address,
    geocode,
    get_geocode_state,
)


class FakeGoogleMapsClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def geocode(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.responses.pop(0)


def make_google_result(state_short_name, formatted_address):
    return {
        "geometry": {
            "location": {"lat": -33.8688, "lng": 151.2093},
            "location_type": "ROOFTOP",
        },
        "formatted_address": formatted_address,
        "place_id": "place-1",
        "partial_match": False,
        "types": ["street_address"],
        "address_components": [
            {
                "long_name": "Australia",
                "short_name": "AU",
                "types": ["country"],
            },
            {
                "long_name": "New South Wales",
                "short_name": state_short_name,
                "types": ["administrative_area_level_1"],
            },
        ],
    }


def test_geocode_falls_back_outside_wa_and_sets_review_flag():
    client = FakeGoogleMapsClient(
        [
            [],
            [make_google_result("NSW", "10 Sample Street, Sydney NSW, Australia")],
        ]
    )

    result = geocode(
        client,
        "10 Sample Street, Australia",
        preferred_state="WA",
        preferred_country="AU",
        fallback_without_state_filter=True,
    )

    assert len(client.calls) == 2
    assert client.calls[0][1]["components"] == {
        "country": "AU",
        "administrative_area": "WA",
    }
    assert "components" not in client.calls[1][1]
    assert result["Google Geocode Status"] == "Success"
    assert result["Google Latitude"] == -33.8688
    assert result["Google Address State"] == "NSW"
    assert result["Google Geocode Preference Flag"] == (
        "Fallback outside preferred state WA - manual review"
    )

    row = pd.Series(
        {
            **result,
            "Address Confidence": "high",
            "Project Address": "10 Sample Street",
        }
    )
    _, _, needs_review = build_final_address(row)
    assert needs_review == "Yes"


def test_get_geocode_state_reads_administrative_area_level_1():
    result = make_google_result("WA", "Perth WA, Australia")

    assert get_geocode_state(result) == "WA"
