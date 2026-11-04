from app.routes.settings import CONFIG_SCHEMA


def test_adanos_sentiment_source_is_a_bounded_select():
    item = next(
        entry
        for entry in CONFIG_SCHEMA["market_intelligence"]["items"]
        if entry["key"] == "ADANOS_SENTIMENT_SOURCE"
    )

    assert item["type"] == "select"
    assert item["default"] == "reddit"
    assert [option["value"] for option in item["options"]] == [
        "reddit",
        "x",
        "news",
        "polymarket",
    ]
