"""
ORION LAB — données « institutionnelles » et de sentiment (sources publiques gratuites).

- Peur & Avidité (alternative.me) : humeur du marché crypto, 0 (peur extrême) à 100 (avidité extrême).
- COT / CFTC (régulateur américain) : positions hebdomadaires des gestionnaires d'actifs sur les
  contrats bitcoin / ether du CME. Publié le vendredi pour le mardi : on décale de 4 jours pour ne
  jamais utiliser une information avant sa publication.
- DVOL (Deribit) : volatilité implicite à 30 jours des options BTC / ETH (la « peur » du marché des options).
Toutes les séries sont journalières, index UTC, mises en cache dans lab/data/alt/.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "alt")


def _get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def _cached(name, fetch):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name + ".csv")
    if not os.path.exists(path):
        fetch().to_csv(path, header=True)
    s = pd.read_csv(path, index_col=0).iloc[:, 0]
    s.index = pd.to_datetime(s.index, utc=True)
    return s


def fear_greed() -> pd.Series:
    def fetch():
        d = _get_json("https://api.alternative.me/fng/?limit=0&format=json")["data"]
        s = pd.Series({pd.to_datetime(int(x["timestamp"]), unit="s", utc=True): float(x["value"]) for x in d})
        return s.sort_index().rename("fear_greed")
    return _cached("fear_greed", fetch)


COT_CODES = {"BTC": "133741", "ETH": "146021"}


def cot_asset_managers(asset: str = "BTC") -> pd.Series:
    """Position nette des gestionnaires d'actifs (contrats longs - courts), disponible à la date de publication."""
    def fetch():
        q = urllib.parse.urlencode({"cftc_contract_market_code": COT_CODES[asset], "$limit": 5000,
                                    "$order": "report_date_as_yyyy_mm_dd"})
        rows = _get_json("https://publicreporting.cftc.gov/resource/gpe5-46if.json?" + q)
        s = pd.Series({pd.to_datetime(r["report_date_as_yyyy_mm_dd"], utc=True) + pd.Timedelta(days=4):
                       float(r["asset_mgr_positions_long"]) - float(r["asset_mgr_positions_short"]) for r in rows})
        return s.sort_index().rename("am_net")
    return _cached(f"cot_{asset}", fetch)


def dvol(currency: str = "BTC") -> pd.Series:
    def fetch():
        out = {}
        end = int(pd.Timestamp.utcnow().timestamp() * 1000)
        start = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
        while end > start:
            url = ("https://www.deribit.com/api/v2/public/get_volatility_index_data?"
                   f"currency={currency}&start_timestamp={start}&end_timestamp={end}&resolution=1D")
            data = _get_json(url)["result"]["data"]
            if not data:
                break
            for ts, o, h, l, c in data:
                out[pd.to_datetime(ts, unit="ms", utc=True)] = c
            first = min(x[0] for x in data)
            if first >= end:
                break
            end = first - 1
        return pd.Series(out).sort_index().rename("dvol")
    return _cached(f"dvol_{currency}", fetch)
