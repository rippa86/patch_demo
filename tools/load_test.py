#!/usr/bin/env python3
"""Simulate shoppers user1..userN hitting the webstore through HAProxy.

Each simulated user loops forever (or until --duration):
  wait offline -> load the shop -> log in -> browse -> maybe add items and
  check out -> log out.

Usage:
    python3 load_test.py                          # 100 users, run until Ctrl-C
    python3 load_test.py --users 20 --duration 300
    python3 load_test.py --buy-chance 0.5 --fast  # more buying, shorter pauses
    python3 load_test.py --ramp-up 20 --hold 120  # all 100 logged in within 20s, held 2 min
"""
import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

PASSWORD = "P@ssw0rd1!"

stats_lock = threading.Lock()
calls = Counter()            # (action, status) -> count
latency = defaultdict(list)  # action -> [seconds]
events = Counter()           # high-level outcomes
online = set()               # users currently logged in
stop = threading.Event()
start_time = time.monotonic()


def record(action: str, status: int, seconds: float) -> None:
    with stats_lock:
        calls[(action, status)] += 1
        latency[action].append(seconds)


def request(base, method, path, action, payload=None, token=None, timeout=15.0):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except Exception:
        body = b""
        status = 0  # connection error / timeout
    record(action, status, time.monotonic() - start)
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    return status, parsed


def pause(lo: float, hi: float) -> bool:
    """Sleep a random time; return False if we should stop."""
    return not stop.wait(random.uniform(lo, hi))


def shopper(n: int, args) -> None:
    base = args.base_url.rstrip("/")
    user = f"user{n}"
    scale = 0.2 if args.fast else 1.0

    # ramp-up: user N arrives at an even slot across --ramp-up seconds,
    # so all users are logged in by the end of the ramp
    if args.ramp_up > 0:
        if stop.wait(args.ramp_up * (n - 1) / max(args.users, 1)):
            return
    elif not pause(0, args.max_offline * scale):
        return
    first_visit = True

    while not stop.is_set():
        request(base, "GET", "/", "page")
        status, products = request(base, "GET", "/api/cart/products", "products")

        status, data = request(base, "POST", "/api/login", "login",
                               {"username": user, "password": PASSWORD})
        if status != 200 or not data or "token" not in data:
            with stats_lock:
                events["login failed"] += 1
            if not pause(5 * scale, 15 * scale):
                return
            continue
        token = data["token"]
        with stats_lock:
            events["logins"] += 1
            online.add(user)

        # stay logged in for --session-min..--session-max seconds, browsing the cart
        session_end = time.monotonic() + random.uniform(args.session_min, args.session_max) * scale
        if first_visit and args.ramp_up > 0:
            # keep the first wave online until everyone has arrived
            session_end = max(session_end, start_time + args.ramp_up + args.hold)
        first_visit = False
        while time.monotonic() < session_end:
            if not pause(1 * scale, 6 * scale):
                break
            request(base, "GET", "/api/cart", "cart", token=token)

        # occasionally buy something
        if (not stop.is_set() and random.random() < args.buy_chance
                and isinstance(products, list) and products):
            picks = random.sample(products, k=random.randint(1, min(3, len(products))))
            items = [{"product_id": p["id"], "qty": random.randint(1, 2)} for p in picks]
            status, _ = request(base, "PUT", "/api/cart", "cart-update",
                                {"items": items}, token=token)
            if status == 200 and pause(1 * scale, 4 * scale):
                status, _ = request(base, "POST", "/api/cart/checkout", "checkout",
                                    {}, token=token)
                with stats_lock:
                    events["orders" if status == 200 else "checkout rejected"] += 1

        request(base, "POST", "/api/logout", "logout", {}, token=token)
        with stats_lock:
            events["logouts"] += 1
            online.discard(user)

        if not pause(args.min_offline * scale, args.max_offline * scale):
            return


def report(started: float, final: bool = False) -> None:
    with stats_lock:
        elapsed = time.monotonic() - started
        total = sum(calls.values())
        errors = sum(c for (a, s), c in calls.items() if s == 0 or s >= 500)
        print(f"\n[{elapsed:6.0f}s] requests={total} ({total / max(elapsed, 1):.1f}/s) "
              f"errors(5xx/conn)={errors} online={len(online)} "
              + " ".join(f"{k}={v}" for k, v in sorted(events.items())))
        if final:
            print(f"\n{'action':<12} {'count':>6} {'avg ms':>8} {'p95 ms':>8}  status codes")
            for action in sorted(latency):
                vals = sorted(latency[action])
                p95 = vals[int(len(vals) * 0.95) - 1] if vals else 0
                codes = ", ".join(f"{s or 'conn-err'}:{c}" for (a, s), c in sorted(calls.items())
                                  if a == action)
                print(f"{action:<12} {len(vals):>6} {1000 * sum(vals) / len(vals):>8.0f} "
                      f"{1000 * p95:>8.0f}  {codes}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://shoppingapp.lab.rippa.io")
    p.add_argument("--users", type=int, default=100, help="simulate user1..userN")
    p.add_argument("--duration", type=int, default=0, help="seconds to run (0 = until Ctrl-C)")
    p.add_argument("--buy-chance", type=float, default=0.25, help="chance a visit ends in checkout")
    p.add_argument("--min-offline", type=float, default=10, help="min seconds between visits")
    p.add_argument("--max-offline", type=float, default=60, help="max seconds between visits")
    p.add_argument("--fast", action="store_true", help="cut all pauses to 20%%")
    p.add_argument("--ramp-up", type=float, default=0,
                   help="log every user in, evenly spread over this many seconds (0 = random arrivals)")
    p.add_argument("--hold", type=float, default=60,
                   help="with --ramp-up: keep the first wave logged in this long after the ramp")
    p.add_argument("--session-min", type=float, default=5, help="min seconds logged in per visit")
    p.add_argument("--session-max", type=float, default=30, help="max seconds logged in per visit")
    p.add_argument("--report-every", type=int, default=10)
    args = p.parse_args()

    ramp = f", ramp-up {args.ramp_up:.0f}s then hold {args.hold:.0f}s" if args.ramp_up else ""
    print(f"Starting {args.users} shoppers against {args.base_url} "
          f"(buy chance {args.buy_chance:.0%}{ramp}, Ctrl-C to stop)")
    global start_time
    started = start_time = time.monotonic()
    threads = [threading.Thread(target=shopper, args=(n, args), daemon=True)
               for n in range(1, args.users + 1)]
    for t in threads:
        t.start()

    try:
        while not stop.is_set():
            if stop.wait(args.report_every):
                break
            report(started)
            if args.duration and time.monotonic() - started >= args.duration:
                stop.set()
    except KeyboardInterrupt:
        print("\nStopping... (logging users out)")
        stop.set()

    for t in threads:
        t.join(timeout=20)
    report(started, final=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
