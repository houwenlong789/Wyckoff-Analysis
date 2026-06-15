from __future__ import annotations

from datetime import date


def test_resolve_trade_date_skips_non_trading_day(monkeypatch, capsys):
    import scripts.sector_continuity_report as mod

    monkeypatch.setattr(mod, "resolve_end_calendar_day", lambda: date(2026, 6, 14))
    monkeypatch.setattr(mod, "is_a_share_trading_day", lambda _day: False)

    assert mod._resolve_trade_date() is None
    assert "非 A 股交易日" in capsys.readouterr().out


def test_update_history_uses_resolved_trade_date():
    import scripts.sector_continuity_report as mod

    history = mod._update_history_with_trade_date(
        {},
        [{"name": "半导体", "pct": 2.5, "net_inflow": 300_000_000}],
        date(2026, 6, 15),
    )

    assert history == {"2026-06-15": {"半导体": {"pct": 2.5, "inflow": 300_000_000}}}


def test_notify_report_sends_feishu(monkeypatch):
    import scripts.sector_continuity_report as mod

    captured: dict[str, str] = {}
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setattr(
        mod,
        "send_feishu_notification",
        lambda webhook, title, content: (
            captured.update({"webhook": webhook, "title": title, "content": content}) or True
        ),
    )

    mod._notify_report("# report", date(2026, 6, 15))

    assert captured == {
        "webhook": "https://example.invalid/webhook",
        "title": "板块延续性报告 2026-06-15",
        "content": "# report",
    }
