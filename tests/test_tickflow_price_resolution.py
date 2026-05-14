from integrations.supabase_recommendation import _resolve_tickflow_quote_price
from scripts.tail_buy_intraday_job import _resolve_quote_price


def test_recommendation_quote_price_prefers_tickflow_last_price() -> None:
    price = _resolve_tickflow_quote_price({"last_price": 42.35, "open": 41.35, "prev_close": 41.91})

    assert price == 42.35


def test_tail_buy_quote_price_does_not_fallback_to_open() -> None:
    assert _resolve_quote_price({"open": 41.35, "prev_close": 41.91}) == 0.0
    assert _resolve_quote_price({"last_price": 42.35, "open": 41.35}) == 42.35
