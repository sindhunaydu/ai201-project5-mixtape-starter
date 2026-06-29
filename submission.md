# Mixtape Bug Hunt — Submission

## Codebase Map

### Main files

**`app.py`** — Flask app factory (`create_app()`). Creates the Flask instance, initializes SQLAlchemy (`db`), registers all route blueprints, and creates tables on first run.

**`models.py`** — Five SQLAlchemy models: `User`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, and `Notification`. Three association tables handle many-to-many relationships: `friendships` (symmetric user pairs), `song_tags`, and `playlist_entries` (adds `position`, `added_by`, `added_at` columns — songs in a playlist have an explicit ordering, not just insertion order). All primary keys are UUID strings.

**`routes/songs.py`** — Endpoints for sharing a song (`POST /songs/`), searching (`GET /songs/search`), listening (`POST /songs/<id>/listen`), and rating (`POST /songs/<id>/rate`). Each route delegates immediately to a service function; no business logic lives in routes.

**`routes/playlists.py`** — Endpoints for creating playlists, adding songs to a playlist, and getting a playlist's song list.

**`routes/users.py`** — Endpoints for user profiles, listening streaks, and notifications.

**`routes/feed.py`** — Endpoints for "Friends Listening Now" and the activity feed.

**`services/streak_service.py`** — Computes and updates a user's consecutive-day listening streak. Compares `last_listened_at` date to today's date to decide increment, no-op, or reset.

**`services/feed_service.py`** — Returns friends who listened recently ("Listening Now") and a paginated activity feed of all friend events. Deduplicates per-friend so only the most recent song per friend appears in "Listening Now."

**`services/search_service.py`** — Searches songs by title or artist using SQL `ILIKE`. Joins to `song_tags` to support tag filtering (currently only used for deduplication).

**`services/notification_service.py`** — Creates `Notification` records and provides `add_to_playlist()` and `rate_song()` helpers that also trigger notifications to the original song sharer.

**`services/playlist_service.py`** — Creates playlists and retrieves their songs ordered by `playlist_entries.position`.

### Patterns

Every route does input parsing and response formatting; all business logic lives in the corresponding service. Services own DB commits. Routes never touch `db.session` directly.

---

## Data Flow — User rates a song

1. Client sends `POST /songs/<song_id>/rate` with `{"user_id": "...", "score": 4}`.
2. `routes/songs.py` parses the JSON, calls `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates score (1–5), fetches `Song` and `User`, upserts a `Rating` row, commits, then creates a `Notification` for the song's original sharer (if it's a different user).
4. The route returns the saved rating as JSON.

---

## Bugs Fixed (all five)

### Issue 1 — Streak resets on Sundays (`streak_service.py`)

**Bug:** `update_listening_streak` had an extra guard: `elif days_since_last == 1 and today.weekday() != 6`. `weekday() == 6` is Sunday, so listening on Sunday after Saturday never incremented the streak — it fell through to the reset branch.

**Fix:** Removed `and today.weekday() != 6`. The condition is simply `days_since_last == 1`.

```python
# before
elif days_since_last == 1 and today.weekday() != 6:

# after
elif days_since_last == 1:
```

---

### Issue 2 — Friends Listening Now shows yesterday's listeners (`feed_service.py`)

**Bug:** `RECENT_THRESHOLD = timedelta(hours=24)` is too broad for a "listening now" feature. Anyone who listened in the last 24 hours — including yesterday — would appear as "listening now."

**Fix:** Changed the threshold to 30 minutes.

```python
# before
RECENT_THRESHOLD = timedelta(hours=24)

# after
RECENT_THRESHOLD = timedelta(minutes=30)
```

---

### Issue 3 — Duplicate songs in search results (`search_service.py`)

**Bug:** `search_songs` joined `song_tags` with `outerjoin` to include tag data. If a song has multiple tags, the join produces one row per tag, so the same song appears in results multiple times.

**Fix:** Added `.distinct()` to the query so each song appears at most once.

```python
# before
.all()

# after
.distinct()
.all()
```

---

### Issue 4 — No notification when a friend rates a song (`notification_service.py`)

**Bug:** `rate_song()` saved the rating and committed but never created a `Notification`. The `add_to_playlist()` function correctly notified the sharer, but `rate_song()` had no equivalent call.

**Fix:** Added a `create_notification()` call after committing the rating, mirroring the pattern in `add_to_playlist()`.

```python
if song.shared_by != user_id:
    create_notification(
        user_id=song.shared_by,
        notification_type="song_rated",
        body=f"{rater.username} rated your song '{song.title}' {score}/5.",
    )
```

---

### Issue 5 — Last song in playlist never shows up (`playlist_service.py`)

**Bug:** `get_playlist_songs` returned `songs[:-1]` — a Python slice that drops the final element of the list. The last song in every playlist was silently excluded.

**Fix:** Changed `songs[:-1]` to `songs`.

```python
# before
return [song.to_dict() for song in songs[:-1]]

# after
return [song.to_dict() for song in songs]
```
