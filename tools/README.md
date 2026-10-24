# Test tools

Plain Python 3 (standard library only). Run from any machine that can reach the shop.

| Script | What it does |
| --- | --- |
| `create_accounts.py` | Creates `user1`..`user100` (password `P@ssw0rd1!`, email `userN@example.test`) via `/api/account`. Existing users are skipped, so it is safe to re-run. |
| `load_test.py` | Simulates those users: log in, browse the cart, sometimes check out, log out. Prints a status line every 10s and a latency/status-code table at the end. |

```bash
python3 tools/create_accounts.py                      # through HAProxy (shoppingapp.lab.rippa.io)
python3 tools/create_accounts.py --verify-login       # also log in as each user

python3 tools/load_test.py                            # 100 users, random arrivals, until Ctrl-C
python3 tools/load_test.py --ramp-up 20 --hold 120    # all 100 logged in within 20s, held 2 min
python3 tools/load_test.py --fast --buy-chance 0.5    # heavier traffic, more orders
python3 tools/load_test.py --base-url http://10.10.4.30 --users 30 --duration 600
```

Watch the effect in Grafana (`http://demo-vm.lab.rippa.io:3000/`): active users per node,
requests per API per node, and Galera writes per node. For a failover test, stop a service on
one frontend mid-run; HAProxy marks it DOWN within ~6s and traffic moves to the other node.

Orders reduce stock; restock from `/admin.html` when checkouts start showing `checkout rejected`.
