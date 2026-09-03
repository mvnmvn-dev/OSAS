"""OSRM route generation tests with mocked HTTP."""
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from services import osrm_client


@pytest.fixture
def app():
    application = create_app()
    application.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret",
        "OSRM_BASE_URL": "http://osrm.test",
        "OSRM_TIMEOUT_SECONDS": 2,
    })
    return application


def test_haversine_positive():
    d = osrm_client.haversine_m(14.73, 121.05, 14.731, 121.051)
    assert d > 0


def test_heuristic_trip_returns_geometry(app):
    with app.app_context():
        points = [
            {"lat": 14.7327, "lng": 121.0575, "code": "SEC"},
            {"lat": 14.73295, "lng": 121.0577, "code": "G2"},
            {"lat": 14.7331, "lng": 121.0584, "code": "S1"},
        ]
        result = osrm_client.heuristic_trip(points, roundtrip=True)
        assert result["engine"] == "heuristic"
        assert result["distance_m"] > 0
        assert result["geometry"]["type"] == "LineString"
        assert len(result["geometry"]["coordinates"]) >= 2


def test_trip_uses_osrm_when_available(app):
    fake = {
        "code": "Ok",
        "waypoints": [
            {"waypoint_index": 0},
            {"waypoint_index": 1},
            {"waypoint_index": 2},
        ],
        "trips": [{
            "distance": 1800.4,
            "duration": 1400.2,
            "geometry": {"type": "LineString", "coordinates": [[121.0575, 14.7327], [121.0577, 14.73295]]},
        }],
    }
    with app.app_context():
        with patch("services.osrm_client.requests.get") as mock_get:
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = fake
            mock_get.return_value = resp
            points = [
                {"lat": 14.7327, "lng": 121.0575},
                {"lat": 14.73295, "lng": 121.0577},
                {"lat": 14.7331, "lng": 121.0584},
            ]
            result = osrm_client.trip(points, roundtrip=True)
            assert result["engine"] == "OSRM"
            assert result["distance_m"] == 1800
            assert result["duration_s"] == 1400
            assert result["geometry"]["type"] == "LineString"


def test_trip_falls_back_when_osrm_down(app):
    with app.app_context():
        with patch("services.osrm_client.requests.get", side_effect=ConnectionError("down")):
            points = [
                {"lat": 14.7327, "lng": 121.0575},
                {"lat": 14.73295, "lng": 121.0577},
            ]
            result = osrm_client.trip(points)
            assert result["engine"] == "heuristic"
            assert result["distance_m"] > 0
