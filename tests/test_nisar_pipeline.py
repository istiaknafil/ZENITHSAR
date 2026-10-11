from nisar_pipeline import DatasetMetadata, find_candidate_backscatter_layers


def test_find_candidate_backscatter_layers_requires_gcov_grid_path() -> None:
    records = [
        DatasetMetadata("/science/LSAR/GCOV/grids/frequencyA/HHHH", (4, 5), "float32", {}),
        DatasetMetadata("/science/LSAR/GSLC/grids/frequencyA/S", (4, 5), "float32", {}),
        DatasetMetadata("/science/LSAR/GCOV/metadata", (), "str", {}),
    ]

    candidates = find_candidate_backscatter_layers(records)

    assert [candidate.path for candidate in candidates] == [
        "/science/LSAR/GCOV/grids/frequencyA/HHHH"
    ]
