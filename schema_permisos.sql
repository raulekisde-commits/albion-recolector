-- =====================================================================
-- Smogg WujuCraft — PARTE 2 de 2: seguridad (Row Level Security)
-- Correr después de schema.sql, en una query nueva del SQL Editor.
-- La clave pública (anon/publishable) solo puede leer; el recolector escribe
-- con la clave secreta, que no está sujeta a estas reglas.
-- =====================================================================
alter table public.tracked_items  enable row level security;
alter table public.market_prices  enable row level security;
alter table public.market_history enable row level security;
alter table public.collector_runs enable row level security;

drop policy if exists lectura_publica on public.tracked_items;
drop policy if exists lectura_publica on public.market_prices;
drop policy if exists lectura_publica on public.market_history;
drop policy if exists lectura_publica on public.collector_runs;

create policy lectura_publica on public.tracked_items  for select using (true);
create policy lectura_publica on public.market_prices  for select using (true);
create policy lectura_publica on public.market_history for select using (true);
create policy lectura_publica on public.collector_runs for select using (true);
