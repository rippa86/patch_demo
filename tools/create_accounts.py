#!/usr/bin/env python3
"""Create test accounts user1..userN through the webstore account API.

Usage:
    python3 create_accounts.py                                  # via HAProxy
    python3 create_accounts.py --base-url http://10.10.4.115:8010   # one API directly
    python3 create_accounts.py --count 100 --verify-login
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

PASSWORD = "P@ssw0rd1!"


def post(url: str, payload: dict, timeout: float = 10.0) -> tuple[int, str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # connection refused, timeout, DNS, ...
        return 0, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://shoppingapp.lab.rippa.io",
                        help="shop URL (HAProxy) or an API host:port")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--domain", default="example.test", help="email domain")
    parser.add_argument("--verify-login", action="store_true",
                        help="also log in as each user after creating it")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    created = exists = failed = 0

    for i in range(1, args.count + 1):
        username = f"user{i}"
        status, body = post(f"{base}/api/account", {
            "username": username,
            "email": f"{username}@{args.domain}",
            "password": PASSWORD,
        })
        if status == 200:
            created += 1
            result = "created"
        elif status == 409:
            exists += 1
            result = "already exists"
        else:
            failed += 1
            print(f"{username}: FAILED ({status}) {body[:200]}", file=sys.stderr)
            if status == 0 and i == 1:
                print("Cannot reach the API at all; check --base-url.", file=sys.stderr)
                return 1
            continue

        if args.verify_login:
            lstatus, lbody = post(f"{base}/api/login",
                                  {"username": username, "password": PASSWORD})
            result += ", login ok" if lstatus == 200 else f", LOGIN FAILED ({lstatus}) {lbody[:120]}"
        print(f"{username}: {result}")

    print(f"\nDone: {created} created, {exists} already existed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
