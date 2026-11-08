-- =====================================================================
-- Smogg WujuCraft — base de precios de Albion Online (NA / West)
-- Pegar entero en Supabase → SQL Editor → Run. Se puede volver a correr:
-- no borra datos.
-- =====================================================================

-- Ítems que sigue el recolector (los carga seed_items.py desde datos_albion.json)
create table if not exists public.tracked_items (
    id          text primary key,              -- ID de mercado, ej. T6_MAIN_FIRESTAFF@1
    name        text,
    kind        text not null default 'item',  -- item | material | journal
    tier        smallint,
    enc         smallint,
    active      boolean not null default true,
    added_at    timestamptz not null default now()
);

-- Última foto de órdenes por ítem y ciudad (calidad 1). Se pisa en cada corrida.
create table if not exists public.market_prices (
    item_id        text not null,
    city           text not null,
    sell_min       integer not null default 0,
    sell_max       integer not null default 0,
    buy_max        integer not null default 0,
    sell_min_date  timestamptz,
    buy_max_date   timestamptz,
    fetched_at     timestamptz not null default now(),
    primary key (item_id, city)
);

-- Historial diario (calidad 1): precio promedio y unidades vendidas por día.
create table if not exists public.market_history (
    item_id     text not null,
    city        text not null,
    day         date not null,
    avg_price   integer not null,
    item_count  integer not null,
    primary key (item_id, city, day)
);
create index if not exists market_history_day_idx on public.market_history (day);

-- Registro de cada corrida del recolector (para ver si anda).
create table if not exists public.collector_runs (
    id            bigserial primary key,
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    ids_requested integer,
    price_rows    integer,
    history_rows  integer,
    status        text not null default 'running',  -- running | ok | error
    error         text
);

-- ---------------------------------------------------------------------
-- Precio estimado (misma regla que albion_sync.py):
--   mediana de los promedios diarios de los últimos 7 días, con 3+ ventas,
--   limitada por la orden de venta más barata (ciudades) o por la mejor
--   orden de compra (Black Market). Sin historial suficiente: en el Black
--   Market se usa la mejor orden de compra; en ciudades queda vacío.
-- ---------------------------------------------------------------------
create or replace view public.estimated_prices
with (security_invoker = true) as
with ref as (
    select coalesce(max(day), current_date) - 7 as cutoff from public.market_history
),
stats as (
    select h.item_id, h.city,
           percentile_cont(0.5) within group (order by h.avg_price) as median_7d,
           sum(h.item_count)::integer as sales_7d
    from public.market_history h, ref
    where h.day >= ref.cutoff and h.avg_price > 0
    group by h.item_id, h.city
)
select
    coalesce(p.item_id, s.item_id)          as item_id,
    t.name,
    t.kind,
    coalesce(p.city, s.city)                as city,
    nullif(p.sell_min, 0)                   as sell_min,
    nullif(p.buy_max, 0)                    as buy_max,
    round(s.median_7d)::integer             as median_7d,
    coalesce(s.sales_7d, 0)                 as sales_7d,
    case
        when coalesce(s.sales_7d, 0) >= 3 and s.median_7d > 0 then
            round(least(
                s.median_7d,
                case when coalesce(p.city, s.city) = 'Black Market'
                     then nullif(p.buy_max, 0) else nullif(p.sell_min, 0) end
            ))::integer
        when coalesce(p.city, s.city) = 'Black Market' then nullif(p.buy_max, 0)
        else null
    end                                     as estimated_price,
    p.sell_min_date,
    p.fetched_at
from public.market_prices p
full join stats s on s.item_id = p.item_id and s.city = p.city
left join public.tracked_items t on t.id = coalesce(p.item_id, s.item_id);

-- ---------------------------------------------------------------------
-- RPC para albion_sync.py: devuelve precios e historial de una lista de IDs
-- ya en el formato que usa el script ({prices: {...}, hist: {...}}).
-- ---------------------------------------------------------------------
create or replace function public.get_market_data(ids text[], days integer default 14)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    select jsonb_build_object(
        'prices', coalesce((
            select jsonb_object_agg(item_id, cities)
            from (
                select item_id,
                       jsonb_object_agg(city, jsonb_build_object(
                           'smin', sell_min,
                           'smax', sell_max,
                           'bmax', buy_max,
                           'sdate', coalesce(to_char(sell_min_date at time zone 'UTC',
                                                     'YYYY-MM-DD"T"HH24:MI:SS'), '')
                       )) as cities
                from market_prices
                where item_id = any(ids)
                group by item_id
            ) x
        ), '{}'::jsonb),
        'hist', coalesce((
            select jsonb_object_agg(item_id, cities)
            from (
                select item_id, jsonb_object_agg(city, points) as cities
                from (
                    select item_id, city,
                           jsonb_agg(jsonb_build_object(
                               't', to_char(day, 'YYYY-MM-DD"T"00:00:00'),
                               'p', avg_price,
                               'c', item_count
                           ) order by day) as points
                    from (
                        select *, row_number() over (
                                   partition by item_id, city order by day desc) as rn
                        from market_history
                        where item_id = any(ids)
                          and day >= current_date - days
                    ) h
                    where rn <= 12
                    group by item_id, city
                ) y
                group by item_id
            ) z
        ), '{}'::jsonb)
    );
$$;

-- ---------------------------------------------------------------------
-- Permisos: cualquiera con la clave "anon" (publishable) puede LEER.
-- Solo el recolector, con la clave "service_role" (secret), puede escribir.
-- ---------------------------------------------------------------------
alter table public.tracked_items  enable row level security;
alter table public.market_prices  enable row level security;
alter table public.market_history enable row level security;
alter table public.collector_runs enable row level security;

do $$
declare t text;
begin
    foreach t in array array['tracked_items','market_prices','market_history','collector_runs'] loop
        if not exists (select 1 from pg_policies
                       where schemaname = 'public' and tablename = t and policyname = 'lectura_publica') then
            execute format('create policy lectura_publica on public.%I for select using (true)', t);
        end if;
    end loop;
end $$;

grant select on public.tracked_items, public.market_prices, public.market_history,
                public.collector_runs, public.estimated_prices to anon, authenticated;
grant execute on function public.get_market_data(text[], integer) to anon, authenticated;
