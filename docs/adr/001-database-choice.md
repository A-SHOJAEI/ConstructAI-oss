# ADR-001: PostgreSQL as Primary Database

## Status
Accepted

## Date
2025-06-15

## Context
ConstructAI requires a database supporting relational data, vector embeddings (pgvector),
time-series data (TimescaleDB), and geospatial queries (PostGIS). The construction domain
has complex relational models (projects, documents, schedules, cost items) that benefit
from strong schema enforcement and ACID transactions.

## Decision
Use PostgreSQL 17 as the single primary database with extensions:
- **pgvector** for document embeddings and semantic search
- **TimescaleDB** for time-series metrics (EVM snapshots, usage metering, sensor data)
- **PostGIS** for geospatial queries (site layouts, delivery routes, camera positions)

## Consequences
- Single database simplifies operations, backup, and disaster recovery
- All data co-located enables efficient JOINs across domains
- Extension ecosystem covers all specialized query needs
- Row Level Security (RLS) enables multi-tenant isolation at the database level
- Trade-off: PostgreSQL requires careful index tuning for large-scale deployments

## Alternatives Considered
- **MongoDB**: Flexible schema but poor for relational joins, no native vector search
- **Separate databases**: Vector DB (Pinecone) + relational (PostgreSQL) + time-series (InfluxDB)
  would increase operational complexity significantly
- **CockroachDB**: Distributed SQL but lacks mature extension ecosystem
