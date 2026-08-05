# Database migrations

Run migrations before starting the API:

```bash
alembic upgrade head
```

Production deployments run migrations as a dedicated one-shot job. API replicas never race to create or mutate tables during application startup.
