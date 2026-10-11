# Assignment I — step-by-step guide and explanations

This guide goes through the brief subsection by subsection. For each one it covers:

* **What is asked**, in plain words
* **How we solve it**: the file and command, and why it is built that way
* **Screenshot checklist**: what must be visible. Every screenshot must also show the
  **taskbar and clock**, the command you ran, and its result.
* **Reference results** from our test run on 3 October 2026. The data counts are
  deterministic, so you will get identical numbers. Timings depend on the machine and
  run, so re-run everything yourself and quote **your own** numbers in the report.
* **Report text**: model explanations for the interpretation questions. Rewrite them
  in your own words around your own numbers. Section 9.3 says you may be asked to
  present and explain this work.

> Machine used for the reference run: Apple M3 (8 cores), 16 GB RAM, macOS 14.6, SSD.
> PostgreSQL 17.11 + TimescaleDB 2.30.2 in Docker (Docker Desktop VM: 8 CPUs, 7.65 GiB,
> no per-container limits); EMQX 6.3.1 in Docker; Python 3.13.7.
> shared_buffers = 1959 MB, work_mem = 7837 kB, max_parallel_workers_per_gather = 4, jit = off.

---

## Key ideas to understand first

| Concept | Meaning in this assignment |
|---|---|
| **Reading** | One meter's average power over a 15-minute interval. `event_time` = start of the interval, `received_time` = when it (in simulation) arrived. |
| **kW vs kWh** | kW (`power_kw`) is a *rate*: how hard the grid is working at a moment. kWh (`energy_kwh`) is an *amount*: power × time. One 15-minute reading has energy = power × 0.25 h. Peaks are a kW question (capacity); bills and totals are kWh (volume). |
| **Hypertable** | A TimescaleDB table automatically split into **chunks** by time. Queries with a time filter only open the chunks that overlap it (*chunk exclusion*). |
| **Chunk alignment** | TimescaleDB aligns chunk boundaries to the Unix epoch in **UTC**. CAT = UTC+2, so 1-day chunks run **02:00 → 02:00 CAT**, not midnight to midnight. A CAT day therefore touches 2 daily chunks. This explains several benchmark results. |
| **Compression** | TimescaleDB rewrites a chunk into column-oriented batches of up to 1,000 rows. `orderby` decides row order inside a batch; `segmentby` puts each meter's rows in separate batches. |
| **Continuous aggregate** | A materialized view kept up to date incrementally: it stores pre-computed sums per bucket so dashboards don't scan 10 M rows. |
| **Warm cache** | The first run (warm-up) pulls data pages into memory; the 5 timed runs then measure mostly CPU work, not disk. |

---

## Setup (before Section 1)

1. Install Docker Desktop. Then, from the assignment folder:
   ```bash
   cp .env.example .env
   ```
   Put your own password in `.env`, then start the containers:
   ```bash
   docker compose up -d
   ```
2. Create the Python environment:
   ```bash
   /opt/homebrew/bin/python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
3. In every terminal, load the connection variables:
   ```bash
   set -a; source .env; set +a
   ```

PostgreSQL is published on **port 5433**, because your Mac already runs a Homebrew
PostgreSQL 14 on 5432. That one has no TimescaleDB, and the assignment needs ≥ 2.10.

---

## 1 Build the ingestion pipeline

### 1.1 Confirm the environment [3]

**How:** `sql/00_create_database.sql` creates `energy_grid` and runs
`ALTER DATABASE energy_grid SET timezone TO 'Africa/Kigali'`, so **every** session
starts in CAT. `sql/01_setup.sql` enables the extension and prints the evidence.
Daily buckets are aligned to CAT midnight with
`time_bucket('1 day', ts, 'Africa/Kigali')`; the last query in that file proves it.
`src/s1_1_environment.py` records CPU, RAM, OS, storage, versions and the four
settings in `results/environment.json`.

**Runtime statement for the report:** PostgreSQL runs **in a Docker container**
inside Docker Desktop's Linux VM. The VM limit is 8 CPUs and about 7.65 GiB RAM. The
container itself has no CPU or RAM limit (`NanoCpus=0 Memory=0`).

**Screenshots:**
* EMQX running: `docker ps`, or the EMQX dashboard at http://localhost:18083
  (default login admin/public, then change it)
* `psql` connected to `energy_grid`
* the `pg_extension` row showing timescaledb 2.30.x
* `session_time_zone = Africa/Kigali`
* the output of `s1_1_environment.py`

### 1.2 Create the database structures [3]

**How:** `sql/02_tables.sql` creates:
* `energy_live`, a regular table
* `energy_readings`, a hypertable with 1-day chunks. It is created with
  `create_default_indexes => FALSE`, followed by two explicit indexes. Section 3 needs
  identical indexes on every table.
* `meters`

There is no primary key on the reading tables, because duplicates must be allowed at
this stage. `src/s1_2_load_meters.py` inserts the 4,000 `meter_metadata()` dictionaries.

**Data dictionary (put this table in the report):**

| Table | Column | PostgreSQL type | Meaning | Unit |
|---|---|---|---|---|
| energy_live / energy_readings | meter_id | BIGINT | 10-digit meter identifier | – |
| | event_time | TIMESTAMPTZ | start of the 15-min measurement interval | CAT timestamp |
| | received_time | TIMESTAMPTZ | simulated arrival time (not DB insert time) | CAT timestamp |
| | power_kw | DOUBLE PRECISION | average power over the interval | kW |
| | voltage_v | DOUBLE PRECISION | voltage | V |
| | current_a | DOUBLE PRECISION | current (power factor 0.95) | A |
| | frequency_hz | DOUBLE PRECISION | grid frequency | Hz |
| | energy_kwh | DOUBLE PRECISION | energy used in the interval ≈ power_kw × 0.25 | kWh |
| meters | meter_id | BIGINT PK | meter identifier | – |
| | region | TEXT | Region 1 … Region 10 (400 meters each) | – |
| | customer_type | TEXT | residential / commercial / industrial | – |
| | base_power_kw | DOUBLE PRECISION | meter's typical power level | kW |
| | power_factor | DOUBLE PRECISION | ratio of real to apparent power (0.95) | none |

**Screenshots:**
* the column list printed by `02_tables.sql`; `\d energy_live`, `\d energy_readings`
  and `\d meters` also work
* the `chunk_interval = 1 day` row
* `rows in meters after commit: 4,000`

### 1.3 Capture and publish a pilot stream [4]

**How:**
* `src/s1_3_subscriber.py` subscribes to `energy/meters/#` at QoS 1, parses the JSON
  and inserts into `energy_live`. Start it first, with `--truncate` so the table
  begins empty.
* `src/s1_3_publisher.py` takes the first 2,000 dictionaries with `itertools.islice`
  and publishes each to `energy/meters/{meter_id}` with `qos=1, retain=False`. A
  message counts as **acknowledged** when the broker's PUBACK fires `on_publish`.
* `src/s1_3_pilot_report.py` prints the four counts in one table, plus sample rows.

**The fault we found (use this for 1.3.4; it is real evidence):**

| Stage | Run 1 (faulty) | Run 2 (fixed) |
|---|---|---|
| Taken from generator | 2,000 | 2,000 |
| Acknowledged by broker | 2,000 | 2,000 |
| Received by subscriber | **1,208** | 2,000 |
| Rows in energy_live | **1,208** | 2,000 |

* **Size of the difference:** 792 messages (39.6%) were lost between the broker and
  the subscriber. Nothing was lost between the generator and the broker.
* **Evidence:** `docker exec emqx emqx ctl broker metrics` showed
  `delivery.dropped.queue_full : 792`. The subscriber log showed 0 parse errors and
  0 insert errors, and no DUP-flag redeliveries.
* **Cause:** version 1 committed each row to PostgreSQL *inside* the MQTT
  `on_message` callback. That blocks paho's network loop, so the subscriber drained
  only about 200 messages/s. The publisher sent 2,000 in about 1.4 s. EMQX keeps a
  per-session queue of at most 1,000 messages (`mqtt.max_mqueue_len`), and when it is
  full the broker discards new messages, even at QoS 1.
* **Fix:** `on_message` now only parses the JSON and puts the row on a Python queue.
  A separate writer thread inserts in batches of 200 and commits. Run 2 delivered
  2,000 at every stage. To reproduce the fault for a screenshot, run
  `--mode per-message`.
* **Duplicates:** the 2,000 messages contain 6 deliberate generator duplicates
  (1,994 distinct pairs). They count as separate messages and rows, as the brief
  requires.

**Screenshots:**
* the publisher's `acknowledged_by_broker 2000`
* the subscriber's `received 2000`
* the report table with the count and sample rows
* optionally, the faulty run and the EMQX metric, as proof of the diagnosis

---

## 2 Capture and prepare the historical dataset

### 2.1 Capture and load the complete stream [4]

**Route chosen: generator → CSV file → `COPY FROM STDIN`** (`src/s2_1_capture_load.py`).
* The generator is consumed one dictionary at a time and written straight to the
  CSV, so memory use stays flat. Progress is printed every 1 M rows.
* The CSV is then streamed to PostgreSQL in 8 MB blocks inside one `COPY`
  transaction. COPY is the fastest bulk-load path, far faster than row-by-row
  INSERT or MQTT for 10.7 M rows.

| Measure | Reference run |
|---|---|
| expected emitted_readings | 10,696,848 |
| dictionaries consumed | 10,696,848 ✔ |
| CSV data rows | 10,696,848 ✔ (1.16 GB) |
| committed rows in energy_readings | 10,696,848 ✔ |
| file generation | 72.9 s |
| database load (first write → commit) | 120.2 s |
| total elapsed (generation start → final commit) | 194.6 s |

`sql/03_load_checks.sql` reported:
* 4,000 distinct meters
* earliest event 2026-09-01 00:00 and latest 2026-09-28 23:45 CAT
* sizes across all 29 chunks: **table data 958 MB, indexes 466 MB, total 1,424 MB**

You may delete `data/energy_readings.csv` after the screenshots; the script
regenerates it.

### 2.2 Investigate data quality [3]

**How:** `sql/04_quality.sql`.
1. Build one row per observed `(meter_id, event_time)` pair (`tmp_pairs`), and the
   planned grid of 4,000 meters × 2,688 slots (`tmp_grid`).
2. Missing pairs = grid rows with **no** matching pair (anti-join).
3. Extra copies = Σ(copies − 1).
4. Delayed = pairs whose `received_time > event_time + 15 min`.

It also checks that no reading lies off the grid.

| Entry | Measured | Expected | Match |
|---|---|---|---|
| planned_readings | 10,752,000 | 10,752,000 | ✔ |
| emitted_readings | 10,696,848 | 10,696,848 | ✔ |
| unique_readings | 10,643,735 | 10,643,735 | ✔ |
| duplicate_readings (extra copies) | 53,113 | 53,113 | ✔ |
| delayed_unique_readings | 213,090 | 213,090 | ✔ |
| missing_readings | 108,265 | 108,265 | ✔ |

* Missing **1.0069%**, duplicates **0.4990%**, delayed **2.0020%**, completeness
  **98.9931%**.
* Identity checks:
  * unique + extra = 10,643,735 + 53,113 = 10,696,848 = emitted ✔
  * planned − unique = 108,265 = missing ✔
* On-time arrivals are exactly 15 minutes after `event_time`; delayed arrivals are
  30–180 minutes after.
* **Five meters with the most missing readings:**

  | Meter | Region | Type | Missing |
  |---|---|---|---|
  | 1000003622 | Region 3 | commercial | 47 |
  | 1000000106 | Region 7 | industrial | 45 |
  | 1000000366 | Region 7 | commercial | 45 |
  | 1000001259 | Region 10 | residential | 44 |
  | 1000000189 | Region 10 | commercial | 43 |

  The expected value is about 27 per meter (1% of 2,688), so these are random tail
  values, not broken meters.

### 2.3 Clean and preserve a consistent benchmark [3]

**How:** `sql/05_clean.sql`.
1. Find the duplicate pairs, and use
   `count(DISTINCT (received_time, power_kw, voltage_v, current_a, frequency_hz, energy_kwh))`
   to test whether the copies of each pair **conflict**.
   * Result: 53,113 pairs, 53,113 extra copies, **0 conflicting**.
2. Delete every copy after the first, using `row_number()` over all eight columns.
   Each row is identified by `(tableoid, ctid)`, its physical position inside its
   chunk. Only pairs whose copies agree are touched, so a conflicting pair could never
   be silently deleted.
3. Run `VACUUM ANALYZE`.

| | Rows | Σ energy_kwh |
|---|---|---|
| Before | 10,696,848 | 3,872,863.893 |
| After | **10,643,735** ✔ | 3,853,704.347 |

Remaining duplicate pairs: 0. The 19,159.5 kWh removed is energy that would have
been double-counted.

**Why missing readings must not become zero (report text):** a missing reading means
*we do not know* the consumption, not that the customer used nothing. The meter, the
network or the head-end may have failed. Filling with 0 would:
* understate energy totals and bills
* create false "unusual drop" anomalies
* teach a forecast model that demand collapses at random
* make completeness look like 100%

Gaps should stay as gaps (NULL or absent). They should be reported through a
completeness measure and, where an estimate is needed, filled by a labelled
estimate such as interpolation or a profile, never silently.

---

## 3 Compare chunk intervals

### 3.1 Create and verify three configurations [3]

`sql/06_chunk_tables.sql` creates `energy_readings_3h` and `energy_readings_week`
with `LIKE energy_readings`, copies the cleaned rows, and builds the **same two
B-tree indexes** on each table: `(event_time DESC)` and `(meter_id, event_time DESC)`.
`sql/07_chunk_checks.sql` prints the evidence.

| Table | Chunk interval | Chunks | First chunk start | Last chunk end |
|---|---|---|---|---|
| energy_readings_3h | 3 h | 225 | 31 Aug 23:00 | 29 Sep 02:00 |
| energy_readings | 1 day | 29 | 31 Aug 02:00 | 29 Sep 02:00 |
| energy_readings_week | 7 days | 5 | 27 Aug 02:00 | 1 Oct 02:00 |

All three tables match:
* 10,643,735 rows and 4,000 meters
* dates 1 Sep 00:00 – 28 Sep 23:45
* Σ power 15,414,817.388 and Σ energy 3,853,704.347
* largest difference between tables 9 × 10⁻⁹, well under the 0.001 tolerance ✔

Chunk boundaries fall on 02:00 CAT (that is, UTC midnight), and weekly chunks start
on Thursdays, because the Unix epoch, 1 Jan 1970, was a Thursday.

### 3.2 Implement and time the five workloads [4]

**How:** the queries are in `sql/08_workloads.sql`, written once with a `:"tbl"`
placeholder so only the table changes. `src/s3_2_benchmark.py` does the timing.
* **Client:** psycopg 3 (Python), one autocommit connection
  (`application_name=benchmark-chunks`)
* **Timer:** `time.perf_counter()`, started just before `execute()` and stopped after
  `fetchall()` has returned the last row
* Runs `ANALYZE` first, then **1 warm-up + 5 measured runs** for each query and table,
  sequentially
* Records the CAT start and end, duration and machine name of every run in
  `results/benchmark_chunks_runs.csv`
* Checks that the results match across tables, within 0.001
* Afterwards, saves `EXPLAIN (ANALYZE, BUFFERS)` plans to `results/plans/`

Notes on the queries:
* **Q2** uses `ORDER BY total DESC, event_time ASC LIMIT 10`. The secondary sort
  makes a tie at 10th place deterministic, and it still returns exactly ten rows.
* **Q1** uses `time_bucket('1 hour', …)`. CAT has a whole-hour offset, so UTC hours
  and CAT hours coincide.

**Reference warm-cache medians (ms):**

| Query | 3 h | 1 day | 1 week | Fastest |
|---|---|---|---|---|
| Q1 hourly avg, 21 Sep | 39.4 | 55.7 | 55.5 | 3 h |
| Q2 top-10 intervals, 22–28 Sep | 152.1 | **137.8** | 169.4 | 1 day |
| Q3 energy per meter, all data | 2,196.4 | 842.2 | **641.0** | week |
| Q4 one meter, one week | 2.96 | 1.28 | **0.96** | week |
| Q5 full scan | 546.5 | 502.8 | **458.5** | week |

Session: 51.7 s from the first warm-up submission to the last measured result. The
minimum and maximum per query are in `results/benchmark_chunks_summary.csv`.

Q1 for `energy_readings` was 27 ms in a later session. Small queries vary between
runs, so quote medians and mention the spread.

**Screenshots:**
* the final timing table and the "Result check … MATCH" table
* one plan per table: open `results/plans/chunks_Q2_<table>.txt`, or run
  `EXPLAIN (ANALYZE, BUFFERS) …` in psql

### 3.3 Explain the chunk results [3]

Keep **observation** (what was measured) apart from **explanation** (why). Chunks
opened per query, counted from the saved plans:

| Query | 3 h | 1 day | week |
|---|---|---|---|
| Q1 (1 CAT day) | 9 | 2 | 1 |
| Q2 / Q4 (7 CAT days) | 57 | 8 | 2 |
| Q3 / Q5 (everything) | 225 | 29 | 5 |

* **Q1.**
  * *Observation:* 3 h was fastest (39 ms against about 55 ms).
  * *Explanation:* one CAT day needs only 9 small 3-hour chunks, so it reads about
    1/8 of a day per chunk with no wasted rows. The 1-day table must open 2 chunks,
    because a CAT day straddles the 02:00 boundary, and filter out about 2/3 of their
    rows. The weekly table opens one chunk that is 7× larger and relies on the time
    index inside it.
  * The differences are small, tens of milliseconds, because all variants use
    chunk exclusion.
* **Q2.**
  * *Observation:* 1 day was fastest (138 ms, against 152 ms for 3 h and 169 ms for
    week).
  * *Explanation:* the 7-day window maps onto 8 daily chunks with little waste. With
    3 h, 57 chunks each add planning and executor overhead. With weekly chunks, 2
    chunks are opened but part of them lies outside the window and must be filtered.
* **Q3 and Q5** (no time filter).
  * *Observation:* week was fastest (Q3: 641 against 842 and 2,196 ms; Q5: 458
    against 503 and 547 ms).
  * *Explanation:* every row must be read, so chunk exclusion cannot help, and the
    cost is per-chunk overhead: a scan node, a partial aggregate and parallel-worker
    coordination for each chunk.
  * The 3-hour table has 225 chunks; Q3 in particular must combine 225 partial
    groupings of 4,000 meters, which is why it is 2.6× slower than daily.
* **Q4.**
  * *Observation:* week was fastest (0.96 ms against 1.28 and 2.96 ms).
  * *Explanation:* the `(meter_id, event_time)` index finds the 672 rows in each
    chunk directly. The cost is one index probe per chunk: 2 probes for week, 8 for
    day, 57 for 3 h.
* **Overall:** small chunks help only short, time-filtered queries; large chunks help
  whole-history scans. **1 day is the best all-round choice.** It is fastest or close
  to fastest everywhere except the 3 h case of Q3, and it keeps chunks around 50 MB,
  which suits compression and retention.

---

## 4 Compare compression strategies

### 4.1 Create the two compressed configurations [4]

**Chosen interval: 1 day** (see 3.3).

**How:** `src/s4_1_compress.py`.
1. **Records one cutoff** = experiment start − 48 h. In the reference run the start
   was 3 Oct 10:24, so the cutoff was 1 Oct 10:24.
2. Creates both tables with the same columns and indexes.
3. Sets
   `timescaledb.compress, compress_orderby = 'event_time DESC', compress_segmentby = ''`
   for the time strategy, and `= 'meter_id'` for the meter strategy. The empty string
   matters: recent TimescaleDB versions otherwise pick a segment-by column
   automatically.
4. Adds `add_compression_policy(..., compress_after => '48 hours')` to each table and
   pauses it at once with `alter_job(id, scheduled => false)`.
5. Copies the cleaned data **one source chunk at a time**. After each chunk is loaded,
   it runs `compress_chunk()` if the chunk's end precedes the cutoff, timing loading
   and compression separately (`results/compression_chunks.csv`).

All September chunks end before the cutoff, so all **29/29 chunks** were compressed in
both tables, and the compressed ranges are identical. `sql/09_compression_checks.sql`
shows the settings, the paused policies and the chunk list. Re-run
`sql/07_chunk_checks.sql`: both compressed tables match 10,643,735 rows and the same
sums.

### 4.2 Measure storage and compression cost [4]

`src/s4_2_storage.py`, using `hypertable_detailed_size`. That function includes the
compressed chunks, their TOAST storage and their indexes.

| Table | Data MB | Index MB | Total MB | Ratio | Saved | Compression time | Compressed chunks | Rows in compressed chunks |
|---|---|---|---|---|---|---|---|---|
| energy_readings (baseline) | 950.9 | 465.9 | 1,416.7 | 1.00 | – | – | 0/29 | 0% |
| energy_time_compressed | 437.8 | 0.9 | 438.7 | **3.23** | **69.0%** | 11.5 s | 29/29 | 100% |
| energy_meter_compressed | 496.2 | 5.5 | 501.7 | **2.82** | **64.6%** | 30.4 s | 29/29 | 100% |

**Why meter segmentation compresses less and takes longer:** a 1-day chunk holds only
96 readings per meter. Segmenting by meter creates 4,000 small batches per chunk
instead of about 380 full 1,000-row batches. Every batch carries metadata and
per-column headers, so there is more overhead, and the work involves sorting by meter
first. Mention this in 4.4.

### 4.3 Repeat the query comparison [4]

Pause the policies (`src/jobs.py pause`). They are already paused after 4.1. Then
run `s3_2_benchmark.py --tag compression …` and `src/s4_3_compare.py`. The baseline
is `energy_readings` (uncompressed, 1-day chunks) measured **in the same session**.

| Query | 3 h | 1 day | week | time-compressed | meter-compressed | Time improvement | Meter improvement |
|---|---|---|---|---|---|---|---|
| Q1 | 39.4 | 27.1 | 55.5 | 18.7 | 232.9 | **+31.1%** | −758.3% |
| Q2 | 152.1 | 116.0 | 169.4 | 95.9 | 60.7 | +17.3% | **+47.7%** |
| Q3 | 2,196.4 | 676.8 | 641.0 | 148.9 | 123.6 | +78.0% | **+81.7%** |
| Q4 | 2.96 | 1.68 | 0.96 | 27.9 | 1.67 | −1,562.8% | **+0.3%** |
| Q5 | 546.5 | 499.9 | 458.5 | **139.6** | 169.7 | **+72.1%** | +66.0% |

Medians are in ms. Improvement = 100 × (baseline − compressed) / baseline, so a
positive value means faster.

Then resume the policies:

```bash
.venv/bin/python src/jobs.py resume energy_time_compressed energy_meter_compressed
```

Take a screenshot showing `scheduled = True`.

### 4.4 Select a storage configuration [3]

**Effect of meter segmentation:**
* **Single meter (Q4):** segmenting by meter keeps Q4 as fast as uncompressed
  (1.67 ms). Batches are stored per meter, so only that meter's batches are
  decompressed. Without segmentation, Q4 must decompress every batch in the week and
  filter, which makes it 17× slower (27.9 ms).
* **All-meter, time-sliced queries (Q1):** the effect is reversed. One CAT day spans
  4,000 small meter batches per chunk that all have to be opened (233 ms). The
  time-ordered table skips whole batches using their min/max `event_time` metadata
  (18.7 ms).
* **Q3 and Q5:** both strategies win by 66–82%, because reading about 3× fewer bytes
  dominates.

**Recommendation (three parts):**
1. **Choice:** 1-day chunks with compression **segmented by meter_id**, ordered by
   `event_time DESC`.
2. **Evidence:** it saves 64.6% of storage (1,417 → 502 MB, ratio 2.82). It speeds up
   per-meter-aggregate and full-history queries by 48–82% (Q2, Q3, Q5). It keeps
   single-meter lookups, the most common customer-service and billing access, at
   1.7 ms instead of 28 ms.
3. **Tradeoff:**
   * Compression takes 2.6× longer (30.4 s against 11.5 s for 28 days) and saves
     4.4 points less storage than time-only ordering.
   * Short all-meter, time-window queries such as Q1 become slow (233 ms). Those
     should therefore be served by the continuous aggregates of Section 6, not by the
     raw table.
   * *If your group judges network-wide dashboards more important than per-customer
     lookups, the time-ordered strategy is equally defensible. State the reason.*

---

## 5 Investigate the utility data

Run `src/s5_analysis.py` (5.1–5.3) and `src/s5_4_anomaly.py` (5.4). Charts are in
`results/figures/`.

### 5.1 Identify demand patterns [3]

* **Daily totals:**
  * weekdays about 141–152 MWh
  * weekends about 112–119 MWh, around 21% lower (red bars in
    `s5_1_daily_weekly_energy.png`)
* **Seven-day periods:**

  | Period | Energy (kWh) | Change from previous |
  |---|---|---|
  | 1–7 Sep | 934,199 | – |
  | 8–14 Sep | 953,715 | +2.1% |
  | 15–21 Sep | 973,230 | +2.0% |
  | 22–28 Sep | 992,560 | +2.0% |

  This is a steady growth trend of about 0.3% per day.
* **Top three 15-minute demand intervals:**

  | Interval (CAT) | Network demand |
  |---|---|
  | Mon 28 Sep 13:00 | 7,993.7 kW |
  | Mon 28 Sep 12:45 | 7,943.9 kW |
  | Mon 28 Sep 13:15 | 7,943.6 kW |

* **Explanation:**
  * The midday peak comes from commercial load, which peaks around 13:00 on
    business days. Industrial load is a flat, large base. Residential load has
    morning (≈07:30) and evening (≈19:00) peaks (`s5_1_daily_profile.png`).
  * The peaks land on the last Monday because the growth trend makes each week
    larger than the one before.
* **Operational implications:**
  * Generation and transformer capacity must cover the weekday midday kW peak, and
    that peak is growing.
  * Maintenance fits best at weekends or at night.
  * Energy procurement (kWh) grows by about 2% per week.
* **kW vs kWh:** the peak is a **kW** figure, the instantaneous load the network must
  carry. The daily and weekly totals are **kWh**, the volume delivered and billed. A
  short, high peak drives capacity cost even if the kWh is small.

### 5.2 Compare regions and customer types [3]

**By customer type:**

| Type | Meters | Energy (kWh) | kWh per meter | Share of total |
|---|---|---|---|---|
| industrial | 406 | 2,220,672 | 5,469.6 | 57.6% |
| commercial | 1,010 | 825,475 | 817.3 | 21.4% |
| residential | 2,584 | 807,557 | 312.5 | 21.0% |

**By region:** every region has 400 meters, so the totals and the per-meter values
rank the same way.
* Highest: Region 8, 1,258.7 kWh per meter
* Lowest: Region 4, 811.0 kWh per meter

**Interpretation:**
* Normalising by meter count removes the size effect between *customer types*: an
  industrial meter uses about 17.5× a residential one.
* Between *regions*, a difference remains after normalisation: Region 8 uses 55% more
  per meter than Region 4. The meter mix explains it. Region 8 has 61 industrial
  meters and Region 4 has 29.
* Regional differences therefore reflect **customer mix, not regional behaviour**.
  Planning should be based on the industrial count and load per region. Comparing
  regions fairly needs normalisation by customer type as well, not only by meter count.

### 5.3 Assess reporting quality [3]

**Completeness:**
* network 98.993%
* regions from 98.969% (Region 6) to 99.013% (Region 8), a spread of only 0.04
  points

This shows that loss is random and evenly spread, not a regional communications
fault.

**21 September:**

| Arrivals used | Energy (kWh) | Readings |
|---|---|---|
| by 00:00 on 22 Sep | 149,400.54 | 379,505 |
| all arrivals | 149,552.72 | 380,036 |

The difference is **152.18 kWh, 0.102%** of the final total. 531 readings arrived
after midnight, all from the last about 3 hours of the day (delays are 30–180 min).

**Explanation:**
* A daily report produced at midnight is understated, and the understatement is
  concentrated in the evening peak.
* Late data is eventually recovered: the totals should be marked *provisional* and
  re-run later, or computed after a cutoff of at least 3 hours.
* Missing readings, about 1%, are **never** recovered. Totals understate true
  consumption by about 1% unless estimated.

### 5.4 Investigate abnormal consumption [3]

**Rule (stated before the labels are used):**
* *expected* = the median `power_kw` of the **same meter, same 15-minute slot of the
  CAT day, same day type (weekday or weekend)** over the 28 days
* flag a reading if `power / expected ≥ 2.0` (spike) or `≤ 0.4` (drop)

**Reasons:**
* Each customer type has its own daily shape, and commercial and industrial load
  drops at weekends. Comparing like with like removes the normal pattern; without the
  day-type split, every commercial weekend would look like a drop.
* The median ignores the one abnormal day.
* Normal noise is ±15% plus a trend of at most 8%, so normal ratios stay within about
  0.8–1.25. A 2.0 / 0.4 band leaves a wide margin while still catching a doubling or a
  collapse of demand.

The flags are stored in table `anomaly_flags` (meter_id, event_time, ratio). Only
**afterwards** does the script call `anomaly_events()`.

**Results:**
* 474 flags: 236 spikes and 238 drops
* **40 of 40 events detected, 0 missed**
* **0 false flags**, which is 0% of the 10,643,261 observed readings outside the
  injected intervals
* **0% of meters affected by false flags**
* The 474 flags cover every *observed* reading inside the 40 three-hour windows. That
  is 480 planned readings minus 6 that were missing.

**Explaining missed events and false alarms:**
* The injected changes (×2.8 and ×0.03) are far outside normal variation, so the rule
  separates them perfectly.
* In real data, missed events would come from milder anomalies (for example ×1.5),
  anomalies hidden by missing readings, or a meter whose anomaly recurs often enough
  to shift its median.
* False alarms would come from public holidays, tariff changes, a new appliance or
  outages, which a fixed-ratio rule cannot tell from fraud or faults.
* Lowering the thresholds would catch milder events at the cost of more false alarms.

---

## 6 Continuous aggregates

### 6.1 Create the three aggregate views [4]

`sql/10_caggs.sql` creates the three views `WITH NO DATA` and
`timescaledb.materialized_only = true`:

| View | Bucket | Grouping key |
|---|---|---|
| `cagg_energy_15m_region` | 15 min | region (via `INNER JOIN meters ON meter_id`) |
| `cagg_energy_hour_region` | CAT hour | region (same join) |
| `cagg_energy_day_meter` | CAT day, `time_bucket('1 day', …, 'Africa/Kigali')` | meter_id |

Each view stores the bucket, its key, `sum_energy_kwh`, `sum_power_kw` and
`reading_count`.

`src/s6_1_refresh.py` refreshes each view over 1 Sep → 29 Sep and measures the time
and size, and **only then** adds the policies (`start_offset 3 days`,
`end_offset 1 hour`, `schedule 15 min`).

| View | Rows | Refresh (s) | Data MB | Index MB | Total MB |
|---|---|---|---|---|---|
| 15m × region | 26,880 | 15.9 | 2.11 | 2.04 | 4.15 |
| hour × region | 6,720 | 22.2 | 0.64 | 0.59 | 1.23 |
| day × meter | 112,000 | 17.6 | 7.41 | 5.15 | 12.55 |

The policies alone never touch September: their window is always the last 3 days
before *now*, which is October. That is why the manual refresh is required.

For the background job status, run `.venv/bin/python src/jobs.py status`.

### 6.2 Test freshness and delayed arrivals [3]

`src/s6_2_freshness.py` creates `freshness_test` and `freshness_hourly`: hourly, no
data, real-time off, no policy. It then walks through the stages.

Reference run at 3 Oct 10:42:
* H = 3 Oct 09:00
* older hour = 1 Oct 09:00
* inserts at 1 Oct 09:10 (1 kWh), 1 Oct 09:25 (0.5 kWh) and 3 Oct 09:10 (2 kWh)

| Stage | Older-hour aggregate | Hour-H aggregate | Why |
|---|---|---|---|
| 1. 1 kWh inserted, only older hour refreshed, real-time off | 1 kWh, 1 row | no bucket | Only what was materialized is visible. |
| 2. +0.5 kWh old, +2 kWh H, no refresh, real-time off | **1 kWh, 1 row** (stale) | no bucket | New rows are not in the materialization. The raw table already shows 1.5 kWh / 2 rows and 2 kWh / 1 row. |
| 3. real-time **on**, no refresh | 1 kWh, 1 row (still stale) | **2 kWh, 1 row** | Real-time unions the materialized data *below the watermark* (1 Oct 10:00) with raw data *above* it. H is above, so it is computed live. The older hour is below, so its late insert is only recorded as an invalidation. |
| 4. both hours refreshed | **1.5 kWh, 2 rows** | 2 kWh, 1 row | The refresh processes the invalidation and materializes H; the watermark moves to 3 Oct 10:00. |

An absent bucket is "not yet computed", **not** zero consumption.

**Which hours could the 6.1 policy refresh at 10:42?** Its window is
[now − 3 days, now − 1 hour) = [30 Sep 10:42, 3 Oct 09:42), and only whole buckets
inside the window are refreshed.
* The **older hour (1 Oct 09:00) is refreshable**, so the late 0.5 kWh would be
  picked up on the next 15-minute run.
* **Hour H (3 Oct 09:00–10:00) is not refreshable**, because its end is after 09:42.
  It becomes refreshable only after 11:00. Until then it is visible only with
  real-time aggregation.

---

## 7 Dashboards

### 7.1 / 7.2 Raw and Aggregate dashboards [3 + 3]

**Tool: Plotly Dash** (`dashboard/app.py`).
* One program, started twice:
  * `--mode raw` → dashboard titled **Raw** on http://127.0.0.1:8050
  * `--mode aggregate` → **Aggregate** on :8051
* Identical layout, panels and filters:
  * a whole-CAT-day date range, inclusive in the picker and converted to an
    exclusive end
  * a meter selector
  * a Refresh button
* All panel SQL is in `dashboard/queries.py`; the source, configuration and queries
  are retained there.

| Panel | Raw source | Aggregate source |
|---|---|---|
| Hourly energy by region (kWh) | energy_readings ⋈ meters | cagg_energy_hour_region |
| Daily network energy (kWh) | energy_readings | Σ cagg_energy_15m_region per CAT day |
| Energy per seven-day period (kWh) | energy_readings, `time_bucket('7 days', …, origin 1 Sep)` | Σ cagg_energy_15m_region, same buckets |
| Daily energy of selected meter (kWh) | energy_readings | cagg_energy_day_meter |
| Completeness by region (%) | count(*) ⋈ meters | Σ reading_count of cagg_energy_15m_region |

In both modes, planned readings = registered meters (from `meters`) × days × 96.

**Screenshots:**
* each running dashboard with all five panels
* the panel queries, from `dashboard/queries.py`

### 7.3 Verify matching results [3]

`src/s7_3_verify.py` runs both modes' queries for 22 Sep → 29 Sep (exclusive) and
meter 1000000000, then matches the rows by key.

| Panel | Rows compared | Keys & counts identical | Max abs diff | Result |
|---|---|---|---|---|
| hourly_regional | 1,680 | ✔ | 0.000000 kWh | PASS |
| daily_network | 7 | ✔ | 0.000000 kWh | PASS |
| weekly_network | 1 (22 Sep period) | ✔ | 0.000000 kWh | PASS |
| meter_daily | 7 | ✔ | 0.000000 kWh | PASS |
| completeness | 10 | ✔ | 0.000000 pp | PASS |

The exported data behind each panel is in `results/s7_panel_*.csv`. Also take
screenshots of the two dashboards side by side with the same filters.

### 7.4 Measure performance and judge the tradeoff [4]

**Procedure:**
1. Pause all jobs:
   ```bash
   .venv/bin/python src/jobs.py pause
   ```
2. Open the Raw dashboard and click **Run timing (1 warm-up + 10)**. It clicks
   Refresh 11 times by itself.
3. Repeat in the same browser for Aggregate.
4. Summarise and resume the jobs:
   ```bash
   .venv/bin/python src/s7_4_summary.py
   ```
   ```bash
   .venv/bin/python src/jobs.py resume
   ```

**Measurement tools:**
* Per-panel query time is measured server-side with `time.perf_counter()`.
* Whole-dashboard time is measured in the browser with `performance.now()`, from the
  click until all five Plotly graphs have drawn the figure tagged with that refresh's
  token, plus two animation frames for the paint.
* Dash has no result cache, so caching is disabled by design: every refresh re-runs
  all five SQL queries.

**Reference medians (min–max), ms:**

| Measure | Raw | Aggregate |
|---|---|---|
| **Whole dashboard** | **3,587** (2,545–4,594) | **536** (79–578) |
| hourly regional | 1,875 | 34 |
| daily network | 997 | 14 |
| weekly network | 2,298 | 16 |
| meter daily | 28 | 6 |
| completeness | 1,265 | 16 |

Aggregate cost from 6.1: 17.9 MB of extra storage (1.3% of the 1,424 MB raw table),
55.7 s of one-off refresh, and a policy job every 15 minutes.

**Judgement:**
* **Yes, the cost is justified.** The dashboard is about 6.7× faster overall, and each
  panel query is about 4–150× faster. The remaining about 0.5 s is almost entirely browser
  rendering.
* In Raw mode, the five panels each scan up to 2.7 M rows **concurrently**, so they
  compete for the same CPU cores. Their individual times, 1–2 s, are much worse than
  the same queries run alone.
* With more users, or a 28-day range, Raw gets worse linearly, while Aggregate stays
  flat.
* The price is small storage, a background refresh, and data that can lag by up to
  about 1 hour plus the 15-minute schedule. Real-time aggregation can close that gap.

---

## 8 Forecast the next day

`src/s8_forecast.py` does all of 8.1–8.4.

### 8.1 Prepare chronological data and features [3]

**Dataset:**
* one row per (region, CAT hour), summing `energy_kwh` of the cleaned readings
* 6,720 rows = 10 regions × 672 hours
* readings per region-hour range from 1,562 to 1,597, against 1,600 planned
* **0 absent region-hours**. The script reindexes onto a complete grid, so an absent
  hour would appear as NaN rather than shifting the lags. Rows whose predictors are
  missing would be excluded.

**Splits (by target timestamp, CAT):**

| Split | Dates | Rows | Usable rows |
|---|---|---|---|
| Train | 1–14 Sep | 3,360 | **1,680** |
| Validation | 15–21 Sep | 1,680 | 1,680 |
| Test | 22–28 Sep | 1,680 | 1,680 |

**Forecast origin** = 00:00 CAT of the target day.

**Features** (all available before the origin):

| Feature | Lag (h) | Window | Earliest usable target |
|---|---|---|---|
| lag_24h (same hour, previous day) | 24 | 1 h | 2 Sep 00:00 |
| lag_48h | 48 | 1 h | 3 Sep 00:00 |
| lag_168h (same hour, previous week) | 168 | 1 h | 8 Sep 00:00 |
| prev_day_total (previous CAT day) | 24–47 | 24 h | 2 Sep 00:00 |
| region, hour of day, day of week | calendar | – | – |

**Lookback = 168 h.** Training rows before exclusion: 3,360; excluded (targets before
8 Sep): 1,680; retained: 1,680. The same rows are used for every configuration.

Why accept losing half the training rows? `lag_168h` and day-of-week carry the
**weekly pattern**: weekend dips and the Monday recovery. Without them, Saturdays and
Mondays cannot be predicted (see 8.4).

**Leakage check (printed):** the smallest gap between origin and source is 1 hour, for
`lag_24h` at 23:00 and for `prev_day_total`, so every source precedes its origin.

Preprocessing (a StandardScaler on the numeric features and one-hot encoding of the
calendar features) is **fitted on training rows only**, inside the sklearn `Pipeline`.

### 8.2 Evaluate the previous-day baseline [4]

Prediction = the same hour of the previous day.
* 1,680 test predictions
* **MAE 42.71 kWh, RMSE 79.40 kWh**

### 8.3 Train and evaluate one regression model [4]

**Model: Ridge regression** (linear regression with an L2 penalty). The shared
predictors are those in 8.1.

| Config | Settings | Train rows | Val rows | Val MAE (kWh) | Val RMSE (kWh) |
|---|---|---|---|---|---|
| R1 | Ridge(alpha=0.1) | 1,680 | 1,680 | **4.432** | 5.643 |
| R2 | Ridge(alpha=1.0) | 1,680 | 1,680 | 4.440 | 5.652 |
| R3 | Ridge(alpha=10) | 1,680 | 1,680 | 4.818 | 6.183 |
| R4 | Ridge(alpha=100) | 1,680 | 1,680 | 7.955 | 11.773 |

R1 is selected (lowest validation MAE) and **frozen**: it is not refitted on
validation or test data. It produces 1,680 test predictions. Test-day lags come from
earlier completed days, which is allowed; the model itself never sees them in training.

### 8.4 Judge forecast usefulness [4]

Error = predicted − actual (kWh).

| | Baseline MAE | Model MAE | Baseline RMSE | Model RMSE |
|---|---|---|---|---|
| **All 1,680** | 42.71 | **4.56** | 79.40 | **5.79** |
| Per region | 34.9–56.0 | 3.7–5.8 | 64.2–102.1 | 4.7–7.2 |

The model's MAE is 88–91% lower in every region (`results/s8_metrics.csv`).

**MAE by test day:**

| Day | Baseline | Model |
|---|---|---|
| Tue–Fri | 4.7–5.3 | 4.6–5.2 |
| **Sat 26** | **135.7** | 3.6 |
| Sun 27 | 3.8 | 3.9 |
| **Mon 28** | **139.5** | 4.9 |

**Peak hours:**
* Six regions peak on Mon 28 Sep at 13:00, and Region 4 peaks on Mon 28 Sep at 18:00.
  In Region 1, the actual is 835.6 kWh:
  * baseline 565.3 (error 270.3)
  * model 826.5 (error 9.1)
* Regions 7, 8 and 9 peak on Wednesday or Friday, where both methods are close.
* The full table is in `results/s8_peak_hours.csv`. The plot is
  `results/figures/s8_forecast_region.png` (Region 8).

**Judgement:**
* **The model clearly improves on the baseline.** On ordinary weekdays the two are
  equal (about 5 kWh, about 0.7% of a typical hourly value). The whole gain comes from
  the **weekday/weekend transitions**, where "yesterday" is the wrong analogue and
  the baseline misses by about 135 kWh per hour. The baseline under-forecasts the
  Monday peak by up to 270 kWh, about 32%, which is the most dangerous kind of
  planning error.
* The model's peak errors are 2–18 kWh (under 2.5%), which is useful for day-ahead
  scheduling and procurement.
* **Limitations:**
  * The targets are *observed* totals, which already miss about 1% of readings, so
    the forecast predicts recorded consumption, not true consumption.
  * There is only one training week (8–14 Sep), so each weekday was seen only once.
    Holidays, weather and new large customers are not represented.
  * **In a live setting**, the lag features for yesterday's last hours would be
    incomplete at midnight. About 2% of readings arrive 30–180 min late; in 5.3 we
    measured −0.1% for the day overall and more in the evening hours. The forecast
    should either run after about 03:00 or use a provisional-data correction.

---

## 5.5 Management recommendation (draft, ≤ 500 words; about 375 here)

> **To:** Operations & IT management  **Re:** Smart-meter analytics findings, September 2026
>
> **Operations.** Network demand peaks on weekday middays (about 13:00), driven by
> commercial load on top of a large, flat industrial base. The highest 15-minute
> demand was 7,994 kW on Monday 28 September, and weekly energy grew steadily by
> about 2% per week (934 → 993 MWh). Capacity plans should therefore be based on the
> weekday midday kW peak, adjusted for this growth. Maintenance should be scheduled at
> weekends, when consumption is about 21% lower. Industrial meters (10% of meters) use
> 58% of the energy, and they explain why Region 8 uses 55% more per meter than
> Region 4. Key-account monitoring for industrial customers would give the largest
> benefit per meter. Our rule-based anomaly check (deviation from the same meter's
> usual load at that time and day type) found all 40 test events with no false
> alarms. We recommend running it daily and investigating flags within 24 hours.
>
> **Data quality and reporting.** About 1% of readings never arrive and 2% arrive up
> to 3 hours late. Daily reports produced at midnight understate consumption (by
> 152 kWh, 0.1%, on 21 September). Gaps must never be treated as zero use. Daily
> figures should be published as provisional at midnight and finalised after 03:00,
> with completeness (currently 98.99%, uniform across regions) reported alongside.
>
> **Storage and dashboards.** Keep raw readings in TimescaleDB hypertables with
> 1-day chunks, compressed after 48 hours with meter segmentation. This cuts storage
> by 65% (1,417 → 502 MB per month of data). Full-history queries become 66–82% faster
> and single-meter lookups stay at about 2 ms. Serve dashboards from continuous
> aggregates: a full refresh takes 0.54 s instead of 3.6 s (6.7× faster), for 18 MB
> of extra storage and a 15-minute refresh job.
>
> **Forecasting.** Adopt the Ridge regression day-ahead forecast. On the test week
> its error was 4.56 kWh MAE per region-hour (RMSE 5.79 kWh), against 42.7 kWh
> (RMSE 79.4 kWh) for the "same as yesterday" rule, an 89% reduction. It is especially
> better around weekends: on Monday 28 September the old rule under-forecast the
> regional peak by up to 270 kWh, against at most 18 kWh for the model. Retrain
> weekly, add holiday information, and run the forecast after late readings have
> arrived.

---

## 9 Submission

### 9.1 Organize the report [2]

One PDF, in subsection order, with each screenshot placed next to its subsection.
Every screenshot needs the taskbar and clock visible (on a Mac, the menu bar clock
and the Dock). Reuse earlier screenshots where a subsection only asks for
interpretation (3.3, 4.4, 5.5).

### 9.2 Make the work reproducible [2]

1. Create a GitHub repository and push this folder. `.gitignore` already excludes
   `.venv`, `.env` and the CSV.
2. Zip the same files.
3. Check that the generator is unchanged. Compare the checksum below with the copy
   your lecturer issued:
   ```bash
   shasum -a 256 smart_meter_simulator.py
   ```
4. The README gives the execution order. Run its **Pilot only** section on a clean
   database and take the screenshot of the four counts at 2,000.

### 9.3 Document contributions [1]

| Name | Student ID | Contributions |
|---|---|---|
| _Member 1_ | _ID_ | _e.g. environment, ingestion pipeline (1–2), README_ |
| _Member 2_ | _ID_ | _e.g. chunk and compression benchmarks (3–4), continuous aggregates (6)_ |
| _Member 3_ | _ID_ | _e.g. analysis and anomalies (5), dashboards (7), forecast (8)_ |

Each member must be able to explain the whole pipeline (9.3.2).
