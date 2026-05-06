from __future__ import annotations

from dataclasses import dataclass

from scripts.db_maintenance import cleanup_recommendation_tracking


@dataclass
class _Response:
    data: list[dict] | None = None
    count: int | None = None


class _FakeTable:
    def __init__(self, client: _FakeClient):
        self.client = client
        self.delete_mode = False
        self.filters: list[tuple[str, int]] = []
        self.limit_value: int | None = None
        self.order_desc = False
        self.want_count = False

    def select(self, _columns: str, *, count: str | None = None):
        self.want_count = count == "exact"
        return self

    def order(self, _column: str, *, desc: bool = False):
        self.order_desc = desc
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def lt(self, column: str, value: int):
        self.filters.append((column, value))
        return self

    def delete(self):
        self.delete_mode = True
        return self

    def execute(self):
        rows = self.client.rows
        for column, value in self.filters:
            rows = [row for row in rows if row[column] < value]

        if self.delete_mode:
            deleted_ids = {id(row) for row in rows}
            self.client.rows = [row for row in self.client.rows if id(row) not in deleted_ids]
            return _Response(data=[])

        ordered = sorted(rows, key=lambda row: row["recommend_date"], reverse=self.order_desc)
        limited = ordered[: self.limit_value] if self.limit_value is not None else ordered
        return _Response(data=limited, count=len(rows) if self.want_count else None)


class _FakeClient:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def table(self, _name: str):
        return _FakeTable(self)


def test_cleanup_recommendation_tracking_keeps_latest_distinct_dates():
    dates = [20260505, 20260503, 20260430, 20260425, 20260420]
    rows = [{"recommend_date": date, "code": code} for date in dates for code in range(2)]
    client = _FakeClient(rows)

    status, count = cleanup_recommendation_tracking(client, keep_dates=3, page_size=2)

    remaining_dates = {row["recommend_date"] for row in client.rows}
    assert status == "ok, keep_dates=3, cutoff=20260430"
    assert count is None
    assert remaining_dates == {20260505, 20260503, 20260430}


def test_cleanup_recommendation_tracking_dry_run_counts_rows_before_cutoff():
    dates = [20260505, 20260503, 20260430, 20260425]
    rows = [{"recommend_date": date, "code": code} for date in dates for code in range(2)]
    client = _FakeClient(rows)

    status, count = cleanup_recommendation_tracking(client, keep_dates=3, page_size=3, dry_run=True)

    assert status == "dry_run, keep_dates=3, cutoff=20260430"
    assert count == 2
    assert len(client.rows) == 8
