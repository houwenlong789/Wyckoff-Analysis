from __future__ import annotations


def test_send_feishu_report(monkeypatch):
    import scripts.holding_diagnosis_job as mod

    captured: dict[str, str] = {}
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setattr(
        mod,
        "send_feishu_notification",
        lambda webhook, title, content: (
            captured.update({"webhook": webhook, "title": title, "content": content}) or True
        ),
    )

    mod._send_feishu_report("# holding report")

    assert captured == {
        "webhook": "https://example.invalid/webhook",
        "title": "持仓诊断",
        "content": "# holding report",
    }
