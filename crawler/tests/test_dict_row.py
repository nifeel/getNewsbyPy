from app.storage.database import DictRow


def test_dict_row_case_insensitive_and_index() -> None:
    row = DictRow({"newsid": 9740379, "title": "hello"})
    assert row["NewsID"] == 9740379
    assert row["title"] == "hello"
    assert row[0] == 9740379
    assert row.get("missing") is None
    assert row.to_dict()["NewsID"] == 9740379
