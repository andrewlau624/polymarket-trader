import pytest


@pytest.fixture(autouse=True)
def _isolated_files(tmp_path, monkeypatch):
    """Keep the inventory cache and in-play observations out of research/."""
    import income_bot
    monkeypatch.setattr(income_bot, "INV_CACHE", str(tmp_path / "inv.json"))
    monkeypatch.setattr(income_bot, "INPLAY_OBS", str(tmp_path / "obs.jsonl"))
