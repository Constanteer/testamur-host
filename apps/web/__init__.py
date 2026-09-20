"""Transition-only hosted application namespace.

This package is not part of the public Testamur core distribution.
"""

# Keep the hosted authentication invariant fail-closed while ``identity.py`` remains
# the transition-era account store.  Importing ``apps.web.identity`` executes this
# package first, so every hosted caller receives the guarded session lookup.
#
# This deliberately re-checks durable verification state on *every* session use:
# verification at sign-in time is not sufficient if account state is later reset.
from . import identity as _identity


def _verified_user_for_session(self: _identity.AccountStore, token: str | None):
    if not token:
        return None
    now = _identity._now()
    session_hash = _identity._session_hash(token)
    with self._connect() as connection:
        row = connection.execute(
            """
            SELECT users.*, sessions.expires_at
            FROM sessions
            JOIN users ON users.user_id = sessions.user_id
            WHERE sessions.session_hash = ?
            """,
            (session_hash,),
        ).fetchone()
        if row is None:
            return None
        if int(row["expires_at"]) <= now or row["email_verified_at"] is None:
            connection.execute("DELETE FROM sessions WHERE session_hash = ?", (session_hash,))
            return None
        connection.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE session_hash = ?",
            (now, session_hash),
        )
        return self._public_user(row)


_identity.AccountStore.user_for_session = _verified_user_for_session
