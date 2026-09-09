from sqlalchemy import text

from apps.api.app.core.database import SessionLocal


def scalar(db, sql: str, **params):
    return db.execute(text(sql), params).scalar_one()


def main() -> None:
    with SessionLocal() as db:
        account_id = scalar(
            db,
            "SELECT id FROM betting_accounts WHERE name = :name",
            name="Betano",
        )

        total_bets = scalar(db, "SELECT count(*) FROM bets WHERE account_id = :id", id=account_id)
        greens = scalar(
            db,
            "SELECT count(*) FROM bets WHERE account_id = :id AND result = 'Green'",
            id=account_id,
        )
        reds = scalar(
            db,
            "SELECT count(*) FROM bets WHERE account_id = :id AND result = 'Red'",
            id=account_id,
        )
        stake = scalar(
            db,
            "SELECT COALESCE(sum(stake), 0) FROM bets WHERE account_id = :id",
            id=account_id,
        )
        returns = scalar(
            db,
            "SELECT COALESCE(sum(return_amount), 0) FROM bets WHERE account_id = :id",
            id=account_id,
        )
        profit_loss = scalar(
            db,
            "SELECT COALESCE(sum(profit_loss), 0) FROM bets WHERE account_id = :id",
            id=account_id,
        )
        unique_matches = scalar(
            db,
            "SELECT count(DISTINCT match_id) FROM bets WHERE account_id = :id AND match_id IS NOT NULL",
            id=account_id,
        )
        live = scalar(
            db,
            """
            SELECT count(*)
            FROM bet_selections s
            JOIN bets b ON b.id = s.bet_id
            WHERE b.account_id = :id AND s.is_live = true
            """,
            id=account_id,
        )
        pregame = scalar(
            db,
            """
            SELECT count(*)
            FROM bet_selections s
            JOIN bets b ON b.id = s.bet_id
            WHERE b.account_id = :id AND s.is_live = false
            """,
            id=account_id,
        )
        snapshots = db.execute(
            text(
                """
                SELECT snapshot_date, starting_balance, ending_balance
                FROM daily_bankroll_snapshots
                WHERE account_id = :id
                ORDER BY snapshot_date
                """
            ),
            {"id": account_id},
        ).all()
        failed_rows = scalar(
            db,
            "SELECT count(*) FROM import_rows WHERE status = 'failed'",
        )

    print("=== IMPORT RECONCILIATION ===")
    print(f"bets_total={total_bets}")
    print(f"green={greens}")
    print(f"red={reds}")
    print(f"stake_total={stake}")
    print(f"return_total={returns}")
    print(f"profit_loss_total={profit_loss}")
    print(f"unique_matches={unique_matches}")
    print(f"pregame_selections={pregame}")
    print(f"live_selections={live}")
    print(f"failed_import_rows={failed_rows}")
    print("=== BANKROLL SNAPSHOTS ===")
    for snapshot in snapshots:
        print(
            f"{snapshot.snapshot_date}: "
            f"start={snapshot.starting_balance} end={snapshot.ending_balance}"
        )


if __name__ == "__main__":
    main()
