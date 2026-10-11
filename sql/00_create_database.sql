-- Section 1.1  Run while connected to the default "postgres" database:
--   psql -d postgres -f sql/00_create_database.sql
DROP DATABASE IF EXISTS energy_grid;
CREATE DATABASE energy_grid;
-- Every new session in this database starts in CAT, so date_trunc/time_bucket
-- with a time zone and ::date casts align days to CAT midnight.
ALTER DATABASE energy_grid SET timezone TO 'Africa/Kigali';
