"""Check that the X session cookies work, without Modal and without an LLM.

  X_AUTH_TOKEN=... X_CT0=... python -m backend.tools.check_x

Prints the posts the X source would fetch. No database is touched.
"""

from __future__ import annotations

import os
import sys

from ..sources import x_posts


def main() -> None:
    missing = [n for n in x_posts.XSource.requires_env if not os.environ.get(n)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)}")
    if os.path.exists(x_posts.ACCOUNTS_DB):
        os.remove(x_posts.ACCOUNTS_DB)  # always use the cookies given now
    posts = x_posts._search(x_posts.QUERIES, None)
    print(f"{len(posts)} posts")
    for p in sorted(posts, key=lambda p: p["id"], reverse=True)[:15]:
        text = " ".join(p["text"].split())[:140]
        print(f"- {p['date'][:16]} | {text} | place={p['place']}")
    if not posts:
        print("No results. A new account can get empty searches until it has some age and activity;")
        print("an expired or wrong cookie also gives no results. Check that the account can search on x.com.")


if __name__ == "__main__":
    main()
