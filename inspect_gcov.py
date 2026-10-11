import earthaccess

short_name = "NISAR_L2_GCOV_PROVISIONAL_V1"

results = earthaccess.search_data(
    short_name=short_name,
    count=1,
)

print("Results returned:", len(results))

if not results:
    print("No matching granules found.")
    raise SystemExit(0)

record = dict(results[0])
umm = record.get("umm", {})

print("Top-level keys:", list(record.keys()))
print("UMM keys:", list(umm.keys()))
print("Temporal extent:", umm.get("TemporalExtent"))
print("Spatial extent:", umm.get("SpatialExtent"))
print("Data granule:", umm.get("DataGranule"))

print("Related URL descriptions:")
for item in umm.get("RelatedUrls", []):
    print({
        key: item.get(key)
        for key in ("Type", "Subtype", "Description")
        if key in item
    })