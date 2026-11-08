# Recolector de precios → Supabase

Base de precios compartida para la hoja **Smogg WujuCraft** (Albion Online, NA / West).

```
Jugadores con el cliente de AODP ──► Albion Online Data Project (público)
                                             │  cada 2 h
                                             ▼
                          GitHub Actions: collector.py
                                             │
                                             ▼
                          Supabase (Postgres): precios + historial
                                             │
                                             ▼
                 albion_sync.py (actualizar_precios.bat) ──► Google Sheet
```

- Los jugadores **no instalan nada nuestro**: solo corren el cliente oficial
  de Albion Online Data Project mientras juegan (ver `JUGADORES.md`).
- El recolector corre solo en GitHub, aunque tu PC esté apagada.
- La base guarda la **última foto de órdenes** y el **historial diario**
  (30 días) de ~7.400 IDs: ítems fabricables T4–T8, materiales y journals.
- Tu planilla lee de la base en segundos en vez de bajar todo de AODP.

## Archivos

| Archivo | Qué hace |
| --- | --- |
| `schema.sql` | Parte 1: tablas y vistas `estimated_prices` y `market_data` |
| `schema_permisos.sql` | Parte 2: seguridad (la clave pública solo lee) |
| `collector.py` | Baja precios e historial de AODP y los guarda en Supabase |
| `seed_items.py` | Carga los IDs a seguir desde `datos_albion.json` |
| `recolector.yml` | Workflow de GitHub Actions: corre `collector.py` cada 2 horas. En GitHub va en `.github/workflows/recolector.yml` |
| `JUGADORES.md` | Guía corta para los jugadores que aportan datos |

## Configuración (una sola vez, ~20 minutos)

### 1. Crear la base en Supabase

1. Entrá a <https://supabase.com> e iniciá sesión con tu cuenta de GitHub o Google.
2. **New project** → nombre `albion-precios` → elegí una contraseña de base
   (guardala) → región **South America (São Paulo)** → plan **Free** → *Create*.
3. Cuando termine de crearse: menú izquierdo **SQL Editor** → **New query** →
   pegá todo `schema.sql` → **Run**. Tiene que decir *Success*.
   Después otra **New query** con todo `schema_permisos.sql` → **Run**.
4. **Project Settings → API Keys** (o *Data API*). Copiá:
   - **Project URL** (`https://xxxx.supabase.co`)
   - **Publishable key** (`sb_publishable_...`) o la clásica **anon** (`eyJ...`): solo lectura.
   - **Secret key** (`sb_secret_...`) o la clásica **service_role** (`eyJ...`):
     **escribe en la base; no la compartas con nadie.**

### 2. Agregar las claves al `.env` del proyecto

Abrí `Crafteo Smogg\.env` con el Bloc de notas y agregá abajo de `SHEET_ID`:

```
PRICE_SOURCE=supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=sb_publishable_xxxxxxxx
SUPABASE_SERVICE_KEY=sb_secret_xxxxxxxx
```

`SUPABASE_KEY` (lectura) la usa la planilla; `SUPABASE_SERVICE_KEY` solo la
usa `seed_items.py` desde tu PC.

### 3. Cargar la lista de ítems

En PowerShell, desde la carpeta del proyecto:

```powershell
.\.venv\Scripts\python.exe recolector\seed_items.py
```

Tiene que decir `7394 IDs ... LISTO. Nuevos: 7394`. Repetilo cada vez que
corras `build_dataset.py` después de un parche.

### 4. Subir el recolector a GitHub

1. Creá un repositorio nuevo en <https://github.com/new>, por ejemplo
   `albion-recolector`. Puede ser **privado**.
2. Subí **el contenido de la carpeta `recolector`** (no la carpeta del proyecto
   entero): en el repo nuevo → *uploading an existing file* → arrastrá
   `collector.py`, `requirements.txt`, `schema.sql`, `seed_items.py`, `README.md`,
   `JUGADORES.md`. El workflow va en una carpeta especial: en el repo →
   **Add file → Create new file** → nombre `.github/workflows/recolector.yml`
   → pegá el contenido de `recolector.yml` → *Commit*.
3. En el repo: **Settings → Secrets and variables → Actions → New repository secret**:
   - `SUPABASE_URL` = tu Project URL
   - `SUPABASE_SERVICE_KEY` = tu Secret key
4. Pestaña **Actions** → *Recolector de precios* → **Run workflow**. La primera
   corrida tarda ~5 minutos. Si termina en verde, ya está: sigue solo cada 2 horas.

> Nunca subas `.env` ni `credenciales.json` a GitHub.

### 5. Actualizar la planilla

Doble clic en `actualizar_precios.bat` como siempre. En el log vas a ver
`Leyendo precios de ... items desde Supabase...` y cuándo fue la última corrida
del recolector. Si Supabase falla, el script baja de AODP directo como antes.
Para volver al modo anterior, poné `PRICE_SOURCE=aodp` en `.env`.

## Consultar la base

En Supabase → **Table Editor** o **SQL Editor**:

```sql
-- Precio estimado de un ítem en todas las ciudades
select * from estimated_prices where item_id = 'T6_MAIN_FIRESTAFF@1';

-- ¿Anduvo el recolector?
select * from collector_runs order by id desc limit 10;
```

`estimated_prices` usa la misma regla que la planilla: mediana de 7 días con
3+ ventas, limitada por la orden de venta más barata (o por la mejor orden de
compra en el Black Market).

## Costos y límites

- **Supabase Free:** 500 MB de base. Con 30 días de historial se usa bastante
  menos. Ojo: los proyectos Free **se pausan tras 7 días sin actividad**; el
  recolector escribe cada 2 h, así que no debería pasar.
- **GitHub Actions:** gratis e ilimitado en repos públicos; en privados hay
  2.000 min/mes y cada corrida usa ~4–6 min (≈1.500 min/mes). Si te quedás
  corto, cambiá el cron a cada 3 horas (`17 */3 * * *`) o hacé el repo público
  (no tiene ningún secreto en el código).
- GitHub **desactiva los cron de repos sin actividad por 60 días**: si pasa, te
  llega un mail y lo reactivás con un clic en *Actions*.
- AODP permite 180 consultas/min y 300 cada 5 min; el recolector hace ~150 por
  corrida, espaciadas 1,1 s.

## Variables opcionales

| Variable | Default | Para qué |
| --- | --- | --- |
| `HISTORY_RETENTION_DAYS` | `30` | Días de historial que se guardan |
| `FULL_HISTORY` | — | `1` = reescribir todo el historial (normalmente solo la 1.ª corrida del día) |
| `AODP_HOST` | servidor West | Otro servidor de AODP |
