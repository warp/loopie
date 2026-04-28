from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class Result:
    ok: bool
    status_code: int | None
    elapsed_ms: float
    prompt_name: str
    error: str | None = None


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs_sorted = sorted(xs)
    if len(xs_sorted) == 1:
        return xs_sorted[0]
    k = (len(xs_sorted) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs_sorted) - 1)
    if f == c:
        return xs_sorted[f]
    d0 = xs_sorted[f] * (c - k)
    d1 = xs_sorted[c] * (k - f)
    return d0 + d1


def _load_prompts(path: Path) -> list[dict[str, str]]:
    prompts: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        obj = json.loads(s)
        prompts.append(
            {
                "name": str(obj.get("name") or "prompt"),
                "message": str(obj.get("message") or ""),
            }
        )
    if not prompts:
        raise SystemExit(f"No prompts found in {path}")
    return prompts


async def _one_request(
    client: httpx.AsyncClient,
    *,
    url: str,
    user_id: str,
    session_id: str,
    prompt_name: str,
    message: str,
    timeout_s: float,
    request_id: str,
) -> Result:
    t0 = time.perf_counter_ns()
    try:
        resp = await client.post(
            url,
            json={"message": message, "user_id": user_id, "session_id": session_id},
            headers={"X-Request-Id": request_id},
            timeout=timeout_s,
        )
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        ok = 200 <= resp.status_code < 300
        return Result(ok=ok, status_code=resp.status_code, elapsed_ms=elapsed_ms, prompt_name=prompt_name)
    except Exception as e:
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        msg = str(e).strip()
        if not msg:
            msg = repr(e)
        return Result(ok=False, status_code=None, elapsed_ms=elapsed_ms, prompt_name=prompt_name, error=msg)


async def main_async() -> int:
    ap = argparse.ArgumentParser(description="Local load test for Loopie /api/chat.")
    ap.add_argument("--base-url", default=os.environ.get("LOOPIE_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--endpoint", default="/api/chat")
    ap.add_argument("--prompts", default="perf/prompts.jsonl")
    ap.add_argument("--requests", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--user-id", default=os.environ.get("DEFAULT_USER_ID", "demo-user"))
    ap.add_argument("--session-prefix", default="perf")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    random.seed(args.seed)
    url = args.base_url.rstrip("/") + args.endpoint
    prompts = _load_prompts(Path(args.prompts))

    limits = httpx.Limits(max_keepalive_connections=args.concurrency, max_connections=args.concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        sem = asyncio.Semaphore(args.concurrency)
        results: list[Result] = []

        async def run_one(i: int) -> None:
            prompt = random.choice(prompts)
            # Make sessions somewhat stable so we hit both existing and new-session paths.
            session_id = f"{args.session_prefix}-{i % max(1, args.concurrency)}"
            request_id = f"perf-{int(time.time())}-{i}"
            async with sem:
                r = await _one_request(
                    client,
                    url=url,
                    user_id=args.user_id,
                    session_id=session_id,
                    prompt_name=prompt["name"],
                    message=prompt["message"],
                    timeout_s=args.timeout_s,
                    request_id=request_id,
                )
                results.append(r)

        await asyncio.gather(*[run_one(i) for i in range(args.requests)])

    timings = [r.elapsed_ms for r in results]
    oks = [r for r in results if r.ok]
    errs = [r for r in results if not r.ok]

    report = {
        "url": url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "ok": len(oks),
        "errors": len(errs),
        "p50_ms": round(_pct(timings, 0.50), 3),
        "p95_ms": round(_pct(timings, 0.95), 3),
        "p99_ms": round(_pct(timings, 0.99), 3),
        "mean_ms": round(statistics.mean(timings) if timings else 0.0, 3),
    }

    print(json.dumps(report, indent=2))

    out_path = args.out.strip()
    if not out_path:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = f".perf/results-{ts}.json"
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "report": report,
                "results": [r.__dict__ for r in results],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out}")

    if errs:
        # Show a few representative errors.
        sample = errs[: min(5, len(errs))]
        for r in sample:
            print(f"ERROR prompt={r.prompt_name} status={r.status_code} elapsed_ms={r.elapsed_ms:.1f} err={r.error}")
        return 2
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()

