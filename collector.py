#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recolector de precios de Albion Online (NA / West) → Supabase.

Baja de Albion Online Data Project (AODP) los precios actuales y el
historial diario de todos los ítems activos en `tracked_items` y los guarda
en Supabase. Lo corre GitHub Actions cada 2 horas; también se puede correr
a mano:

    python collector.py                # corrida completa
    python collector.py --limit 50     # prueba rápida con 50 IDs
    python collector.py --dry-run      # baja de AODP pero no escribe en Supabase

Variables de entorno (o archivo .env en esta carpeta o en la de arriba):
    SUPABASE_URL           https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY   clave secreta (service_role / sb_secret_...)
    AODP_HOST              opcional, por defecto el servidor West
    HISTORY_RETENTION_DAYS opcional, por defecto 30
"""

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

AODP_HOST = "https://west.albion-online-data.com/api/v2/stats"
LOCATIONS = [
    "Thetford", "Lymhurst", "Martlock", "Fort Sterling", "Bridgewatch",
    "Caerleon", "Brecilien", "Black Market",
]
AODP_BATCH = 100          # IDs por request a AODP
AODP_PAUSE = 1.1          # segundos entre requests (límite AODP: 300 cada 5 min)
UPSERT_CHUNK = 1000       # filas por request a Supabase
USER_AGENT = "smogg-wujucraft-collector/1.0"


def log(msg):
    print(msg, flush=True)


def load_env():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, ".env"), os.path.join(here, "..", ".env")):
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8-sig"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Supabase (PostgREST)
# ---------------------------------------------------------------------------
class Supabase:
    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/rest/v1"
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })
        # Claves clásicas (JWT "eyJ...") van también como Bearer; las nuevas
        # (sb_secret_... / sb_publishable_...) solo en el header apikey.
        if not key.startswith("sb_"):
            self.session.headers["Authorization"] = f"Bearer {key}"

    def _check(self, response):
        if response.status_code >= 300:
            raise RuntimeError(
                f"Supabase HTTP {response.status_code}: {response.text[:300]}"
            )
        return response

    def select_all(self, table, params, page=1000):
        rows = []
        offset = 0
        while True:
            query = dict(params, limit=page, offset=offset)
            response = self._check(self.session.get(f"{self.base}/{table}", params=query))
            chunk = response.json()
            rows.extend(chunk)
            if len(chunk) < page:
                return rows
            offset += page

    def has_rows(self, table):
        response = self._check(self.session.get(
            f"{self.base}/{table}", params={"select": "*", "limit": 1}
        ))
        return bool(response.json())

    def upsert(self, table, rows, on_conflict):
        for n in range(0, len(rows), UPSERT_CHUNK):
            chunk = rows[n:n + UPSERT_CHUNK]
            for attempt in range(5):
                response = self.session.post(
                    f"{self.base}/{table}",
                    params={"on_conflict": on_conflict},
                    headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                    json=chunk,
                )
                if response.status_code < 300:
                    break
                if response.status_code >= 500 and attempt < 4:
                    time.sleep(3 * (attempt + 1))
                    continue
                self._check(response)

    def insert_returning(self, table, row):
        response = self._check(self.session.post(
            f"{self.base}/{table}",
            headers={"Prefer": "return=representation"},
            json=row,
        ))
        return response.json()[0]

    def update(self, table, match, values):
        self._check(self.session.patch(
            f"{self.base}/{table}",
            params={k: f"eq.{v}" for k, v in match.items()},
            headers={"Prefer": "return=minimal"},
            json=values,
        ))

    def delete(self, table, filters):
        self._check(self.session.delete(
            f"{self.base}/{table}",
            params=filters,
            headers={"Prefer": "return=minimal"},
        ))


# ---------------------------------------------------------------------------
# AODP
# ---------------------------------------------------------------------------
def aodp_get(session, url, params, tries=8):
    for attempt in range(tries):
        try:
            response = session.get(url, params=params, timeout=90)
        except Exception as exc:
            log(f"    error de red ({exc}); reintento {attempt + 1}/{tries}")
            time.sleep(5)
            continue
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                log("    respuesta no-JSON; reintento")
                time.sleep(3)
                continue
        text = (response.text or "")[:200]
        if response.status_code == 429 or "Throttled" in text:
            log("    AODP limitó las consultas; espero 60 s...")
            time.sleep(60)
            continue
        log(f"    HTTP {response.status_code}: {text}")
        time.sleep(5)
    raise RuntimeError(f"No se pudo descargar de AODP: {url}")


def to_ts(value):
    """AODP usa '0001-01-01T00:00:00' cuando no hay dato."""
    if not value or value.startswith("0001"):
        return None
    return value if value.endswith("Z") or "+" in value[10:] else value + "Z"


def price_rows(data, fetched_at):
    rows = []
    for row in data:
        if row.get("quality", 1) != 1:
            continue
        smin = int(row.get("sell_price_min") or 0)
        bmax = int(row.get("buy_price_max") or 0)
        if smin <= 0 and bmax <= 0:
            continue
        rows.append({
            "item_id": row["item_id"],
            "city": row["city"],
            "sell_min": smin,
            "sell_max": int(row.get("sell_price_max") or 0),
            "buy_max": bmax,
            "sell_min_date": to_ts(row.get("sell_price_min_date")),
            "buy_max_date": to_ts(row.get("buy_price_max_date")),
            "fetched_at": fetched_at,
        })
    return rows


def history_rows(data, since):
    rows = {}
    for row in data:
        # El endpoint devuelve todas las calidades; se usa solo calidad 1.
        if row.get("quality", 1) != 1:
            continue
        for point in row.get("data", []):
            try:
                day = datetime.fromisoformat(point["timestamp"].replace("Z", "")).date()
            except Exception:
                continue
            if day < since:
                continue
            price = int(point.get("avg_price") or 0)
            if price <= 0:
                continue
            key = (row["item_id"], row["location"], day)
            rows[key] = {
                "item_id": row["item_id"],
                "city": row["location"],
                "day": day.isoformat(),
                "avg_price": price,
                "item_count": int(point.get("item_count") or 0),
            }
    return list(rows.values())


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------
def main():
    load_env()
    dry_run = "--dry-run" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit("Faltan SUPABASE_URL y/o SUPABASE_SERVICE_KEY.")
    host = os.environ.get("AODP_HOST", AODP_HOST).rstrip("/")
    retention = int(os.environ.get("HISTORY_RETENTION_DAYS", "30"))

    db = Supabase(url, key)
    ids = sorted(r["id"] for r in db.select_all(
        "tracked_items", {"select": "id", "active": "eq.true"}
    ))
    if limit:
        ids = ids[:limit]
    if not ids:
        raise SystemExit("tracked_items está vacía. Corré seed_items.py primero.")
    log(f"{len(ids)} IDs a consultar en {host}")

    run = None
    if not dry_run:
        run = db.insert_returning("collector_runs", {"ids_requested": len(ids)})

    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        fetched_at = datetime.now(timezone.utc).isoformat()
        since = date.today() - timedelta(days=retention)
        # Los días viejos del historial casi no cambian: normalmente se
        # reescriben solo los últimos 3 días. El historial completo se guarda
        # si la tabla está vacía, en la primera corrida del día (antes de las
        # 02:00 UTC, para completar ítems nuevos o corridas fallidas) o con
        # FULL_HISTORY=1.
        full = (
            os.environ.get("FULL_HISTORY") == "1"
            or datetime.now(timezone.utc).hour < 2
            or dry_run
            or not db.has_rows("market_history")
        )
        upsert_since = since if full else date.today() - timedelta(days=3)
        log("Historial: " + ("completo" if full else "últimos 3 días"))
        prices, history = [], []
        batches = range(0, len(ids), AODP_BATCH)

        log("Bajando precios actuales...")
        for i, n in enumerate(batches):
            batch = ids[n:n + AODP_BATCH]
            data = aodp_get(session, f"{host}/prices/{','.join(batch)}.json",
                            {"locations": ",".join(LOCATIONS), "qualities": 1})
            prices.extend(price_rows(data, fetched_at))
            if i % 10 == 0:
                log(f"  {min(n + AODP_BATCH, len(ids))}/{len(ids)}")
            time.sleep(AODP_PAUSE)

        log("Bajando historial diario...")
        for i, n in enumerate(batches):
            batch = ids[n:n + AODP_BATCH]
            data = aodp_get(session, f"{host}/history/{','.join(batch)}.json",
                            {"locations": ",".join(LOCATIONS), "time-scale": 24})
            history.extend(history_rows(data, upsert_since))
            if i % 10 == 0:
                log(f"  {min(n + AODP_BATCH, len(ids))}/{len(ids)}")
            time.sleep(AODP_PAUSE)

        log(f"{len(prices)} filas de precios · {len(history)} filas de historial")
        if dry_run:
            log("DRY-RUN: no se escribió nada en Supabase.")
            return

        log("Guardando en Supabase...")
        db.upsert("market_prices", prices, "item_id,city")
        db.upsert("market_history", history, "item_id,city,day")
        db.delete("market_history", {"day": f"lt.{since.isoformat()}"})

        db.update("collector_runs", {"id": run["id"]}, {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "price_rows": len(prices),
            "history_rows": len(history),
            "status": "ok",
        })
        log("LISTO.")
    except Exception as exc:
        if run:
            try:
                db.update("collector_runs", {"id": run["id"]}, {
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "error": str(exc)[:1000],
                })
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
