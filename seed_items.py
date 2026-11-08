#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carga en Supabase (tabla tracked_items) todos los IDs de mercado de
datos_albion.json: ítems fabricables, materiales y journals vacíos/llenos.

Correrlo una vez al configurar, y de nuevo cada vez que se regenere
datos_albion.json con build_dataset.py. No borra nada: los IDs que ya no
estén en el dataset quedan con active = false.

Uso (desde la carpeta del proyecto):
    .\\.venv\\Scripts\\python.exe recolector\\seed_items.py
    .\\.venv\\Scripts\\python.exe recolector\\seed_items.py --data otra\\ruta\\datos_albion.json

Necesita SUPABASE_URL y SUPABASE_SERVICE_KEY en el .env del proyecto.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collector import Supabase, load_env, log  # noqa: E402


def build_rows(data):
    rows = {}
    for item in data["items"]:
        rows[item["id"]] = {
            "id": item["id"], "name": item["name"], "kind": "item",
            "tier": item["tier"], "enc": item["enc"], "active": True,
        }
    for mat in data["materiales"]:
        mid = mat["market_id"]
        rows.setdefault(mid, {
            "id": mid, "name": mat["nombre"], "kind": "material",
            "tier": mat["tier"], "enc": mat["enc"], "active": True,
        })
    for journal in data["journals"]:
        for key, name_key in (("empty_id", "nombre_vacio"), ("full_id", "nombre_lleno")):
            rows.setdefault(journal[key], {
                "id": journal[key], "name": journal[name_key], "kind": "journal",
                "tier": journal["tier"], "enc": 0, "active": True,
            })
    return list(rows.values())


def main():
    load_env()
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "datos_albion.json")
    if "--data" in sys.argv:
        path = sys.argv[sys.argv.index("--data") + 1]
    if not os.path.exists(path):
        raise SystemExit(f"No encuentro {path}. Corré build_dataset.py primero.")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit("Faltan SUPABASE_URL y/o SUPABASE_SERVICE_KEY en .env")

    data = json.load(open(path, encoding="utf-8"))
    rows = build_rows(data)
    log(f"{len(rows)} IDs en {os.path.basename(path)} "
        f"(generado {data.get('generado', '?')})")

    db = Supabase(url, key)
    current = {r["id"] for r in db.select_all("tracked_items", {"select": "id"})}
    db.upsert("tracked_items", rows, "id")

    stale = sorted(current - {r["id"] for r in rows})
    for n in range(0, len(stale), 200):
        chunk = stale[n:n + 200]
        quoted = ",".join('"' + s.replace('"', '') + '"' for s in chunk)
        db.session.patch(
            f"{db.base}/tracked_items",
            params={"id": f"in.({quoted})"},
            headers={"Prefer": "return=minimal"},
            json={"active": False},
        ).raise_for_status()

    log(f"LISTO. Nuevos: {len(rows) - len(current & {r['id'] for r in rows})} · "
        f"desactivados: {len(stale)}")


if __name__ == "__main__":
    main()
