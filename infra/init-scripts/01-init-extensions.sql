-- ConstructAI PostgreSQL Extensions Initialization
-- Runs once on first database creation

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";           -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS "postgis";          -- PostGIS for spatial queries
CREATE EXTENSION IF NOT EXISTS "pg_trgm";          -- Trigram for BM25-style text search
-- TimescaleDB is pre-installed in timescale/timescaledb-ha image
-- Hypertables created in later migrations when IoT tables are added
