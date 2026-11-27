from app.openapi.register import enrich_spec


def test_advertisement_contract_is_generated_for_each_method():
    spec = enrich_spec({"paths": {"/api/settings/advertisements": {"get": {}, "put": {}}}})
    operations = spec["paths"]["/api/settings/advertisements"]
    for operation in operations.values():
        assert operation["security"] == [{"HumanJWT": []}]
        assert {"200", "401", "403"}.issubset(operation["responses"])
    assert "requestBody" not in operations["get"]
    assert "409" in operations["put"]["responses"]
    schema = operations["put"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["items", "revision"]
    assert schema["properties"]["revision"]["minimum"] == 0
    assert schema["properties"]["items"]["maxItems"] == 40
    item = schema["properties"]["items"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert item["properties"]["position"]["enum"] == ["sidebar", "accounts"]
    assert item["properties"]["image_url"]["format"] == "uri"


def test_advertisement_docs_do_not_create_missing_routes():
    assert enrich_spec({"paths": {}}) == {"paths": {}}
