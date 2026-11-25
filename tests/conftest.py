import os
import sys

import pytest

# Pas d'horloge en arrière-plan (ni d'appel réseau) pendant les tests.
os.environ["GARDIEN_CLOCK_ENABLED"] = "false"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _offline_gardien(monkeypatch, tmp_path):
    """Portefeuille simulé dans un dossier temporaire et prix Kraken factice (aucun appel réseau)."""
    monkeypatch.setenv("GARDIEN_DATA_DIR", str(tmp_path / "gardien_data"))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)  # jamais de vraie notification pendant les tests
    monkeypatch.setenv("GARDIEN_STATE_FILE", str(tmp_path / "gardien_pf.json"))
    import gardien_sim
    monkeypatch.setattr(gardien_sim, "fetch_price", lambda pair="XBTEUR": 100.0)
    monkeypatch.setattr(gardien_sim, "fetch_ticker",
                        lambda pair="XBTEUR": {"prix": 100.0, "ouverture_jour": 100.0, "plus_bas_jour": 100.0})
