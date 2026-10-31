"""Simple load check (not part of the normal test run).

    python tests/load/load_test.py http://127.0.0.1:8000 200

Sends N parallel requests to /health, /api/catalog and the home blocks, then prints how many failed and how long
the slowest ones took. It only reads data. It measures the machine it runs against: a result from a laptop says
nothing about the production server (see docs/LOAD.md)."""
import asyncio
import statistics
import sys
import time

import httpx

PATHS = ["/health", "/api/catalog?limit=20", "/api/catalog?category=job&limit=8", "/api/catalog?q=диван",
         "/api/geo", "/api/fields", "/api/banners"]


async def one(client: httpx.AsyncClient, path: str) -> tuple[str, int, float]:
    started = time.perf_counter()
    try:
        r = await client.get(path)
        return path, r.status_code, time.perf_counter() - started
    except Exception:  # noqa: BLE001
        return path, 0, time.perf_counter() - started


async def main(base: str, total: int) -> int:
    limits = httpx.Limits(max_connections=total, max_keepalive_connections=total)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:
        started = time.perf_counter()
        results = await asyncio.gather(*(one(client, PATHS[i % len(PATHS)]) for i in range(total)))
        wall = time.perf_counter() - started
    times = sorted(t for _, code, t in results if code == 200)
    bad = [(p, c) for p, c, _ in results if c != 200]
    print(f"requests: {total}, ok: {len(times)}, failed: {len(bad)}, total time: {wall:.2f} s, rate: {total / wall:.0f} req/s")
    if times:
        print(f"median: {statistics.median(times) * 1000:.0f} ms, p95: {times[int(len(times) * 0.95) - 1] * 1000:.0f} ms, max: {times[-1] * 1000:.0f} ms")
    for path, code in bad[:5]:
        print("failed:", path, code)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000",
                              int(sys.argv[2]) if len(sys.argv) > 2 else 200)))
