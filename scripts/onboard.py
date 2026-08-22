"""Emergency/manual onboarding writer; self-install is the normal path."""

from __future__ import annotations

import argparse

from google.cloud import firestore
from weave_ingestion.firestore_client import MeetingLedger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--dm-space")
    parser.add_argument("--project")
    args = parser.parse_args()
    if not args.user_id.isdigit():
        parser.error("--user-id must be a numeric Cloud Identity id")
    if args.dm_space and not args.dm_space.startswith("spaces/"):
        parser.error("--dm-space must use the spaces/{id} resource format")
    return args


def main() -> None:
    args = parse_args()
    client = firestore.Client(project=args.project) if args.project else firestore.Client()
    user = MeetingLedger(client).upsert_onboarded_user(
        user_id=args.user_id,
        email=args.email,
        dm_space=args.dm_space,
    )
    print(f"onboarded {user.email} as {user.user_id}")


if __name__ == "__main__":
    main()
