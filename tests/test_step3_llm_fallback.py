from __future__ import annotations

import pandas as pd


def test_call_track_report_falls_back_to_efficiency_after_gemini_failure(monkeypatch):
    import scripts.step3_batch_report as step3

    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setenv("EFFICIENCY_API_KEY", "eff-key")
    monkeypatch.setenv("EFFICIENCY_MODEL", "eff-model")
    monkeypatch.setenv("EFFICIENCY_BASE_URL", "https://efficiency.example/v1")
    monkeypatch.setattr(step3, "GEMINI_MODEL_FALLBACK", "gemini-backup")

    def fake_call_llm(**kwargs):
        calls.append((kwargs["provider"], kwargs["model"], kwargs.get("base_url")))
        if kwargs["provider"] == "gemini":
            raise RuntimeError("gemini unavailable")
        return "## 💀 逻辑破产\n- 无\n\n## ⏳ 储备营地\n- 无\n\n## 🏹 处于起跳板\n- 000001"

    monkeypatch.setattr(step3, "call_llm", fake_call_llm)

    ok, report, used_model = step3._call_track_report(
        track="Trend",
        system_prompt="system",
        user_message="user",
        model="gemini-main",
        api_key="gem-key",
        selected_codes=["000001"],
        selected_df=pd.DataFrame([{"code": "000001"}]),
        provider="gemini",
    )

    assert ok is True
    assert "处于起跳板" in report
    assert used_model == "Efficiency:eff-model"
    assert calls == [
        ("gemini", "gemini-main", None),
        ("gemini", "gemini-backup", None),
        ("efficiency", "eff-model", "https://efficiency.example/v1"),
    ]


def test_step3_llm_routes_allow_efficiency_when_gemini_key_missing(monkeypatch):
    import scripts.step3_batch_report as step3

    monkeypatch.setenv("EFFICIENCY_API_KEY", "eff-key")
    monkeypatch.setenv("EFFICIENCY_MODEL", "eff-model")
    monkeypatch.setenv("EFFICIENCY_BASE_URL", "https://efficiency.example/v1")

    routes = step3._build_step3_llm_routes(
        provider="gemini",
        model="gemini-main",
        api_key="",
        llm_base_url="",
    )

    assert routes == [
        {
            "provider": "efficiency",
            "model": "eff-model",
            "api_key": "eff-key",
            "base_url": "https://efficiency.example/v1",
        }
    ]
