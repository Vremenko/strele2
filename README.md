# Arhiv strel — agregati za Slovenijo

Zbira **število udarcev** (brez shranjevanja natančnih koordinat) iz [Meteoinfo API](https://test.meteoinfo.si/openapi.json):

- po **dnevih** in **urah** (celotna Slovenija)
- po **statističnih regijah** (12 regij, GURS)

## Zahteve

- Python 3.11+
- PostgreSQL 14+
- GeoJSON regij: `data/SR.geojson` ([gurs-rpe](https://github.com/stefanb/gurs-rpe), CC-BY 4.0)

## Namestitev

```powershell
cd c:\Users\rok99\OneDrive\Namizje\strelko
.\setup.ps1
.\.venv\Scripts\Activate.ps1
copy .env.example .env
```

> **Pomembno:** ukaze za Python vedno poženi prek `.venv` (ne samo `py`), npr.:
> `.\.venv\Scripts\python.exe -m strele_archive.backfill_24h`
> ali kratko: `.\backfill.ps1`

**Baza (Docker):**

```powershell
docker compose up -d
Get-Content sql/schema.sql | docker compose exec -T db psql -U postgres -d strele_archive
```

Uredi `.env` (`DATABASE_URL`, po potrebi `API_BASE_URL`).

## Zagon

**Začetni uvoz zadnjih ~24 h:**

```powershell
py -m strele_archive.backfill_24h
```

**En poll (test):**

```powershell
py -m strele_archive.poll_once
```

**Stalni ingest (cron / storitev):**

```powershell
py -m strele_archive.ingest
```

Priporočen interval: **5 minut** (`POLL_INTERVAL_SEC=300`).

## Izvoz za graf

```powershell
# zadnjih 30 dni
py -m strele_archive.export si-daily --days 30

# urno za dan
py -m strele_archive.export si-hourly --date 2026-06-23

# regije za dan
py -m strele_archive.export regije-daily --date 2026-06-23

# CSV
py -m strele_archive.export si-daily --days 30 --format csv
```

## Spletna stran z grafi

```powershell
.\start.ps1
```

Odpri [http://127.0.0.1:8080](http://127.0.0.1:8080) — **ne odpiraj** `web/index.html` neposredno (API ne bo deloval).

- **Grafi** — `/`
- **Zemljevid občin** — `/map` (obarvani poligoni, podatki ob premiku miške)

- **dnevno** — zadnjih 7 / 14 / 30 / 90 dni
- **urno** — profil za izbran dan
- **regije** — razdelitev po statističnih regijah

API endpointi: `/api/si-daily`, `/api/si-hourly?day=…`, `/api/regije-daily?day=…`

## Kako deluje

1. `GET /api/v1/strele` z bbox Slovenije (~24 h podatkov)
2. Deduplikacija v `strele_dedup` (TTL ~26 h)
3. Point-in-polygon → statistična regija (**štejejo se le udari znotraj meja SI**)
4. `COUNT++` v agregatnih tabelah, koordinate se ne arhivirajo

## Omejitve

- Zgodovina se zbira **od dneva zagona** — `backfill_24h` napolni zadnjih ~24 h iz API.
- Vir API ima okno **~24 h**; pogost poll zmanjša tveganje vrzeli.
- Preveri pogoje uporabe Meteoinfo API pred produkcijo.

## Struktura

```
sql/schema.sql           — tabele
strele_archive/ingest.py — stalni worker
strele_archive/export.py — izvoz JSON/CSV
strele_archive/server.py — API + spletna stran
web/index.html           — grafi (Chart.js)
data/SR.geojson          — meje regij
```
