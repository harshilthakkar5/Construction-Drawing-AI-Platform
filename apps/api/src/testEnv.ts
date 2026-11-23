// Pure tests import modules that load env.ts, which refuses to start without these.
// Placeholders only: no unit test connects, and the database tests read RFI_TEST_DATABASE_URL.
process.env.DATABASE_URL ??= "postgresql://test-placeholder@localhost:1/none";
process.env.REDIS_URL ??= "redis://localhost:1";
process.env.QDRANT_URL ??= "http://localhost:1";
