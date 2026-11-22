"""La version de ccxt (bibliothèque qui parle à Kraken) est figée : Render installe exactement celle qu'on a testée."""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_ccxt_is_pinned_and_matches_installed():
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    m = re.search(r"^ccxt==([0-9.]+)", req, re.M)
    assert m, "ccxt doit être figé avec == dans requirements.txt"
    ccxt = pytest.importorskip("ccxt")
    assert ccxt.__version__ == m.group(1), "les tests doivent tourner avec la version figée de ccxt"
