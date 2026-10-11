from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend
from nisar_streaming import _discover_georeferencing


class FakeGranule(dict):
    def __init__(self) -> None:
        super().__init__(
            {
                "meta": {"native-id": "fake-product", "provider-id": "ASF"},
                "umm": {
                    "GranuleUR": "fake-product",
                    "TemporalExtent": {
                        "RangeDateTime": {
                            "BeginningDateTime": "2025-01-01T00:00:00Z",
                            "EndingDateTime": "2025-01-01T00:00:10Z",
                        }
                    },
                    "RelatedUrls": [{"Type": "GET DATA", "URL": "https://example.test/fake.h5"}],
                },
                "size": 1024,
            }
        )

    def data_links(self) -> list[str]:
        return ["https://example.test/fake.h5"]


def test_health_and_collection_endpoints() -> None:
    client = TestClient(backend.app)
    assert client.get("/api/health").json()["status"] == "ok"
    collections = client.get("/api/earthdata/collections").json()["collections"]
    assert collections[0]["short_name"] == "NISAR_L2_GCOV_PROVISIONAL_V1"


def test_search_normalizes_realistic_granule(monkeypatch) -> None:
    monkeypatch.setattr(backend, "discover_gcov", lambda **_: [FakeGranule()])
    client = TestClient(backend.app)
    response = client.post(
        "/api/earthdata/observations/search",
        json={"collection": "NISAR_L2_GCOV_PROVISIONAL_V1", "count": 1},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 1
    assert result["observations"][0]["product_id"] == "fake-product"
    assert result["observations"][0]["size_mb"] == pytest.approx(1024.0)
    assert result["observations"][0]["source"] == "NASA CMR via earthaccess"


def test_archive_search_paginates_and_reports_timeline(monkeypatch) -> None:
    monkeypatch.setattr(
        backend,
        "discover_gcov",
        lambda **_: [FakeGranule(), FakeGranule()],
    )
    client = TestClient(backend.app)
    response = client.post(
        "/api/earthdata/archive/search?page=1&page_size=1",
        json={"collection": "NISAR_L2_GCOV_PROVISIONAL_V1"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 1
    assert result["has_more"] is True
    assert result["timeline"]["earliest_acquisition"] == "2025-01-01T00:00:00Z"
    assert result["inventory_complete"] is False


def test_archive_search_returns_empty_timeline(monkeypatch) -> None:
    monkeypatch.setattr(backend, "discover_gcov", lambda **_: [])
    response = TestClient(backend.app).post(
        "/api/earthdata/archive/search?page=1&page_size=20",
        json={"collection": "NISAR_L2_GCOV_PROVISIONAL_V1"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 0
    assert result["timeline"]["available_dates"] == []
    assert result["inventory_complete"] is True


def test_search_validates_bbox_and_date_order() -> None:
    client = TestClient(backend.app)
    response = client.post(
        "/api/earthdata/observations/search",
        json={
            "start": "2025-02-01T00:00:00Z",
            "end": "2025-01-01T00:00:00Z",
            "bbox": {"west": 2, "south": 1, "east": 1, "north": 2},
        },
    )
    assert response.status_code == 422


def test_product_inspection_requires_registered_safe_product(tmp_path: Path, monkeypatch) -> None:
    try:
        import h5py
    except ImportError as exc:
        pytest.skip(f"h5py native library unavailable: {exc}")
    product = tmp_path / "product.h5"
    with h5py.File(product, "w") as handle:
        handle.create_dataset(
            "science/LSAR/GCOV/grids/frequencyA/HHHH",
            data=[[1.0, 2.0], [3.0, 4.0]],
        )
    monkeypatch.setattr(backend, "DOWNLOAD_ROOT", tmp_path)
    backend.app.state.products["fixture"] = product
    client = TestClient(backend.app)
    response = client.post("/api/products/fixture/inspect")
    assert response.status_code == 200
    assert response.json()["candidate_count"] == 1


def test_full_download_is_disabled() -> None:
    response = TestClient(backend.app).post(
        "/api/earthdata/observations/unknown/download",
    )
    assert response.status_code == 410


def test_stream_requires_at_most_five_granules() -> None:
    response = TestClient(backend.app).post(
        "/api/earthdata/stream",
        json=["a", "b", "c", "d", "e", "f"],
    )
    assert response.status_code == 422


def test_stream_writes_only_derived_outputs(monkeypatch) -> None:
    backend.app.state.observations["stream-fixture"] = backend.Observation(
        id="stream-fixture",
        product_id="fixture",
        collection="NISAR_L2_GCOV_BETA_V1",
        data_urls=[],
    )
    backend.app.state.granules["stream-fixture"] = object()
    monkeypatch.setattr(
        backend,
        "stream_granule",
        lambda _granule, product_id, **_: {
            "product_id": product_id,
            "preview": "data/streamed/fixture.png",
        },
    )
    response = TestClient(backend.app).post(
        "/api/earthdata/stream",
        json=["stream-fixture"],
    )
    assert response.status_code == 200
    assert response.json()["storage"].startswith("Only derived previews")


def test_change_requires_two_matching_observations(monkeypatch) -> None:
    for observation_id, product_id in (("before", "before-product"), ("after", "after-product")):
        backend.app.state.observations[observation_id] = backend.Observation(
            id=observation_id,
            product_id=product_id,
            collection="NISAR_L2_GCOV_BETA_V1",
            data_urls=[],
        )
        backend.app.state.granules[observation_id] = object()
    monkeypatch.setattr(
        backend,
        "stream_change_pair",
        lambda *_args, **_kwargs: {
            "changed_percent": 12.5,
            "changed_pixels": 10,
            "threshold_db": 3,
            "preview": "data/streamed/change.png",
            "warning": "candidate only",
        },
    )
    response = TestClient(backend.app).post(
        "/api/earthdata/change?frequency=frequencyA&polarization=HH&threshold_db=3",
        json=["before", "after"],
    )
    assert response.status_code == 200
    assert response.json()["result"]["changed_percent"] == 12.5


def test_change_rejects_mixed_maturity(monkeypatch) -> None:
    backend.app.state.observations["beta"] = backend.Observation(
        id="beta", product_id="beta", collection="NISAR_L2_GCOV_BETA_V1", data_urls=[]
    )
    backend.app.state.observations["provisional"] = backend.Observation(
        id="provisional",
        product_id="provisional",
        collection="NISAR_L2_GCOV_PROVISIONAL_V1",
        data_urls=[],
    )
    response = TestClient(backend.app).post(
        "/api/earthdata/change",
        json=["beta", "provisional"],
    )
    assert response.status_code == 422


def test_georeferencing_discovery_reads_coordinate_ranges(tmp_path: Path) -> None:
    try:
        import h5py
    except ImportError as exc:
        pytest.skip(f"h5py native library unavailable: {exc}")
    product_path = tmp_path / "coordinates.h5"
    with h5py.File(product_path, "w") as product:
        grid = product.create_group("science/LSAR/GCOV/grids/frequencyA")
        grid.create_dataset("xCoordinates", data=[100.0, 101.0, 102.0])
        grid.create_dataset("yCoordinates", data=[20.0, 19.0, 18.0])
        grid.attrs["epsg"] = 32646
        discovered = _discover_georeferencing(product)
    assert discovered["coordinates"]["xCoordinates"]["range"] == [100.0, 102.0]
    assert discovered["coordinates"]["yCoordinates"]["range"] == [20.0, 18.0]
    assert discovered["bounds"] == {"xmin": 100.0, "ymin": 18.0, "xmax": 102.0, "ymax": 20.0}
    assert discovered["projection_attributes"]["epsg"] == 32646
