"""Étape 4 : le portefeuille simulé et le journal du gardien survivent à un redémarrage."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien_sim as G  # noqa: E402


def test_save_creates_folder_and_roundtrips(tmp_path):
    path = str(tmp_path / "disque" / "sous_dossier" / "pf.json")
    G.save(path, {"euros": 100.0, "note": "€"})
    assert G.load(path) == {"euros": 100.0, "note": "€"}
    assert not os.path.exists(path + ".tmp")


def test_corrupt_file_is_set_aside_not_deleted(tmp_path):
    path = tmp_path / "pf.json"
    path.write_text("{abîmé", encoding="utf-8")
    assert G.load(str(path)) is None
    assert not path.exists()
    kept = [p for p in os.listdir(tmp_path) if p.startswith("pf.json.corrompu-")]
    assert len(kept) == 1 and (tmp_path / kept[0]).read_text(encoding="utf-8") == "{abîmé"


def test_missing_file_returns_none(tmp_path):
    assert G.load(str(tmp_path / "absent.json")) is None


def test_is_durable():
    assert G.is_durable("/var/data/gardien_portefeuille.json")
    assert not G.is_durable("/tmp/gardien_portefeuille.json")
    assert not G.is_durable("/tmp")


def _start_app(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.delenv("GARDIEN_STATE_FILE", raising=False)
    monkeypatch.setenv("GARDIEN_DATA_DIR", str(tmp_path / "disque"))
    sys.modules.pop("app", None)
    import app as A
    A._gardien_cache.update({"ts": 0.0, "data": None, "candles": None})
    return A


def test_portfolio_and_journal_survive_restart(monkeypatch, tmp_path):
    from test_gardien import series
    A = _start_app(monkeypatch, tmp_path)
    assert A.GARDIEN_STATE_FILE == str(tmp_path / "disque" / "gardien_portefeuille.json")
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    monkeypatch.setattr(A.gardien_sim, "fetch_price", lambda pair: 400.0)
    assert A._gardien_tick()["operation_simulee"].startswith("ACHAT")

    # « Redémarrage » : on recharge le serveur depuis zéro, même dossier de stockage.
    B = _start_app(monkeypatch, tmp_path)
    monkeypatch.setattr(B.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    monkeypatch.setattr(B.gardien_sim, "fetch_price", lambda pair: 400.0)
    assert len(B._gardien_checks) == 1  # journal relu
    pf = B.app.test_client().get("/gardien/portefeuille").get_json()
    assert pf["en_position"] is True and len(pf["trades"]) == 1  # portefeuille relu
    assert B._gardien_tick().get("operation_simulee") is None  # pas de second achat le même jour
    journal = B.app.test_client().get("/gardien/journal").get_json()
    assert journal["horloge"]["nb_verifications_enregistrees"] == 2
    assert "durable" in journal["stockage"]
