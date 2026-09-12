# Standalone one-file version

`savana_tat.py` does the Savana sweep on its own — no config, no adapters, no
storage layout. Use it for a quick one-off when you don't want the full harness.

    python3 savana_tat.py --spu 2345362 --pincodes ../pincodes_tms_master.csv --limit 50
    python3 savana_tat.py --spu 2345362 --pincodes ../pincodes_tms_master.csv

Resumable the same way: rerun the identical command.

For anything recurring, use the main tool in the parent directory instead — it
has the circuit breaker, trend tracking and per-brand config that this one lacks.
