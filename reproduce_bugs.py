"""
reproduce_bugs.py — demonstrate the three bugs before fixes.

Run with: python reproduce_bugs.py
Requires the DB to be seeded: python seed_data.py
"""

from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, Tag, Playlist, ListeningEvent, playlist_entries, song_tags
from sqlalchemy import asc, desc

app = create_app()

with app.app_context():

    # -------------------------------------------------------------------------
    # Issue #1 — Streak resets on Sundays
    # Root cause: update_listening_streak had `today.weekday() != 6` in the
    # increment branch, so Sunday (weekday 6) fell through to the reset branch.
    # -------------------------------------------------------------------------
    print("=" * 60)
    print("ISSUE #1: Streak resets on Sundays")
    print("=" * 60)

    def buggy_streak(last_date_str, today_date_str, current_streak):
        """Replicate the original buggy logic."""
        from datetime import date
        today = date.fromisoformat(today_date_str)
        last = date.fromisoformat(last_date_str)
        days_since = (today - last).days
        if days_since == 0:
            return current_streak, "no change (listened today)"
        elif days_since == 1 and today.weekday() != 6:   # BUG: skips Sundays
            return current_streak + 1, "incremented"
        else:
            return 1, "RESET"

    # Simulate: user listened Saturday, now it's Sunday
    streak, reason = buggy_streak("2026-06-27", "2026-06-28", 5)
    print(f"  Last listened: Saturday 2026-06-27, streak=5")
    print(f"  Today is Sunday 2026-06-28  →  streak={streak} ({reason})")
    print(f"  Expected: 6 (increment), Got: {streak}")

    # Verify Sunday: June 28 2026
    from datetime import date
    d = date(2026, 6, 28)
    print(f"  Confirm: 2026-06-28 weekday={d.weekday()} (6=Sunday ✓)" if d.weekday() == 6 else "  ERROR: not Sunday")

    print()

    # -------------------------------------------------------------------------
    # Issue #3 — Duplicate songs in search results
    # Root cause: outerjoin on song_tags multiplies rows for songs with N tags.
    # Search "rap" matches Crown Heights Anthem (3 tags) → appears 3 times.
    # -------------------------------------------------------------------------
    print("=" * 60)
    print("ISSUE #3: Duplicate songs in search results")
    print("=" * 60)

    query = "Borough"  # matches "Crown Heights Anthem" by "Borough Kings" (3 tags: rap, hip-hop, boom bap)
    buggy_results = (
        db.session.query(Song)
        .outerjoin(song_tags, Song.id == song_tags.c.song_id)
        .filter(
            db.or_(
                Song.title.ilike(f"%{query}%"),
                Song.artist.ilike(f"%{query}%"),
            )
        )
        .all()   # no .distinct()
    )

    print(f"  Search query: '{query}'")
    print(f"  Songs returned (with duplicates): {len(buggy_results)}")
    for s in buggy_results:
        print(f"    - {s.title} by {s.artist}  (id: {s.id[:8]}...)")

    # Count unique
    unique_ids = {s.id for s in buggy_results}
    print(f"  Unique songs: {len(unique_ids)}")
    print(f"  Duplicate rows: {len(buggy_results) - len(unique_ids)}")
    print()

    # -------------------------------------------------------------------------
    # Issue #5 — Last song in playlist never shows up
    # Root cause: get_playlist_songs returned songs[:-1], dropping the last one.
    # -------------------------------------------------------------------------
    print("=" * 60)
    print("ISSUE #5: Last song in playlist never shows up")
    print("=" * 60)

    # Pick the first playlist
    playlist = db.session.query(Playlist).first()
    songs_all = (
        db.session.query(Song)
        .join(playlist_entries, Song.id == playlist_entries.c.song_id)
        .filter(playlist_entries.c.playlist_id == playlist.id)
        .order_by(asc(playlist_entries.c.position))
        .all()
    )

    buggy_return = songs_all[:-1]   # original bug: slice off last song

    print(f"  Playlist: '{playlist.name}'")
    print(f"  Songs actually in playlist: {len(songs_all)}")
    print(f"  Songs returned by buggy code (songs[:-1]): {len(buggy_return)}")
    print(f"  Missing song: '{songs_all[-1].title}' by {songs_all[-1].artist}")
    print()
    print("  All songs (correct order):")
    for i, s in enumerate(songs_all, 1):
        marker = "  ← MISSING from buggy output" if i == len(songs_all) else ""
        print(f"    {i}. {s.title}{marker}")
