from app.models import GeoPoint, RoadAvoidances
from app.utils.cache_manager import _make_key


def test_make_key_normalizes_geopoint_for_args_and_kwargs():
    point = GeoPoint(lat=41.0082, lon=28.9784)

    positional = _make_key("route", (point,), {})
    keyword = _make_key("route", (), {"point": point})

    assert positional == "route:41.0082,28.9784"
    assert keyword == "route:point=41.0082,28.9784"


def test_make_key_normalizes_lists_of_geopoints_stably():
    waypoints = [
        GeoPoint(lat=40.8438, lon=31.1565),
        GeoPoint(lat=40.7650, lon=30.3940),
    ]

    key_a = _make_key("route", (), {"waypoints": waypoints})
    key_b = _make_key("route", (), {"waypoints": list(waypoints)})

    assert key_a == key_b
    assert key_a == "route:waypoints=[40.8438,31.1565,40.765,30.394]"


def test_make_key_normalizes_pydantic_models_with_sorted_fields():
    avoidances_a = RoadAvoidances(avoid_tolls=True, avoid_ferries=False)
    avoidances_b = RoadAvoidances(avoid_ferries=False, avoid_tolls=True)

    key_a = _make_key("route", (), {"avoidances": avoidances_a})
    key_b = _make_key("route", (), {"avoidances": avoidances_b})

    assert key_a == key_b
    assert "avoid_tolls:True" in key_a
    assert "avoid_ferries:False" in key_a
