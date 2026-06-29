# Mixtape Bug Hunt — Submission

## AI Usage

I used Claude (claude-sonnet-4-6 via Claude Code) throughout this project. Here's specifically what I asked it to do and where I had to verify or push back.

**Codebase orientation.** I gave Claude the full contents of each service file and asked "What is this module responsible for? What are its main functions and what does each one do?" This gave me a fast read on which services owned which concerns (streak math vs. notification dispatch vs. playlist ordering) without having to trace every import by hand. The summaries were accurate — I spot-checked them against the code and they held up.

**Data flow tracing.** Before looking at any service, I asked Claude to trace the call chain for "user rates a song" and "user views a playlist" given the routes and services directories. This matched what I found manually: route parses request → delegates to service → service writes DB → service triggers side effects (notifications). Useful for confirming I hadn't missed a layer.

**Explaining specific constructs.** For Issue 1, once I spotted `today.weekday() != 6` I asked "What does Python's `datetime.weekday()` return for each day of the week?" to confirm that 6 = Sunday and not Monday (I always mix up `weekday()` vs `isoweekday()`). The answer was correct and I verified it with `date(2026,6,28).weekday()` in a Python shell.

**Comparing two code paths.** For Issue 4, I pasted `add_to_playlist()` and `rate_song()` side by side and asked "What's the structural difference between these two functions?" Claude immediately identified that `add_to_playlist` calls `create_notification` and `rate_song` doesn't. I had already noticed this myself from reading, but the comparison confirmed I wasn't missing a subtler path where notification gets triggered.

**Where I had to verify myself.** For Issue 3 (duplicate search results), Claude's initial explanation was that the `outerjoin` would cause the same song to appear multiple times in the Python result list. That turned out to be incomplete — SQLAlchemy 2.0's identity map deduplicates ORM objects, so the symptom is masked at the Python layer. I caught this by actually running the buggy query and seeing only 1 result returned despite 3 raw SQL rows. I then ran raw SQL directly to confirm the duplication exists at the database level. Claude's diagnosis pointed me to the right code location, but the "visible duplicate in the list" framing was wrong for this SQLAlchemy version.

**What I did not use AI for.** I did not ask Claude to find the bugs by reading the code. I read each service file myself first, formed a hypothesis about where the bug was, then used AI to help understand specific constructs or compare patterns after I had already located the suspicious code. The one time I tried asking "what's wrong with this function?" before reading it carefully myself, the answer was plausible-sounding but pointed at the wrong line.

---

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

## Root Cause Analyses (three chosen bugs)

---

### Issue 1 — Streak resets on Sundays (`streak_service.py`)

**How I reproduced it:** The bug only fires when today is a Sunday. Simulated by calling the original `update_listening_streak` logic with `last_listened_at = Saturday 2026-06-27` and `now = Sunday 2026-06-28` (`date(2026,6,28).weekday() == 6` confirmed). With a current streak of 5, the function returned streak=1 (RESET) instead of 6 (increment).

**How I found the root cause:** The issue title said "streak keeps resetting" → README pointed to `streak_service.py`. Read the route first: `POST /songs/<id>/listen` in `routes/songs.py` calls `streak_service.record_listening_event()`. That function calls `update_listening_streak(user, now)`. Read `update_listening_streak` top-down and wrote down each branch: `days_since_last == 0` (no-op), `days_since_last == 1 and today.weekday() != 6` (increment), else (reset). The `weekday()` guard in the increment branch immediately stood out — there's no streak rule about the day of the week. Verified by checking Python docs: `weekday()` returns 6 for Sunday, so this guard silently blocks the increment every Sunday and falls through to reset.

**Root cause:** In `update_listening_streak` (`streak_service.py:73`), the increment branch had an extra condition `and today.weekday() != 6`. Python's `datetime.weekday()` returns 6 for Sunday, so any time a user listened on Sunday after listening on Saturday (`days_since_last == 1`), the condition evaluated to `False` and fell through to the `else` branch, which resets the streak to 1. The streak rule says nothing about day of week — consecutive calendar days should always increment.

**Fix and side-effect check:** Removed `and today.weekday() != 6` so the branch is simply `elif days_since_last == 1:`. Checked the other two branches: the no-op (`days_since_last == 0`) and reset (`else`) branches are untouched and still correct. Verified the fix with the existing test suite — `test_streak_increments_on_sunday` passes. Also confirmed the Saturday→Monday path (days_since_last == 2) still resets correctly.

---

### Issue 4 — No notification when a friend rates a song (`notification_service.py`)

**How I reproduced it:** Used seed data: nova shared "Midnight Drive", simone is nova's friend. Ran the original `rate_song` logic (save `Rating`, commit — no `create_notification` call). Queried `Notification` for nova with `notification_type='song_rated'` before and after — count stayed at 0 new entries despite the rating being saved successfully to the DB.

**How I found the root cause:** Issue said "notified when added to playlist but not when rated" → this pointed to two parallel code paths in the same service. README call chain: `POST /songs/<id>/rate` → `routes/songs.py` → `notification_service.rate_song()`. Read `routes/songs.py` to confirm the call. Read `notification_service.py` and compared `add_to_playlist()` and `rate_song()` side by side. `add_to_playlist()` ends with `create_notification(...)` after the DB write. `rate_song()` ends with `db.session.commit()` and `return rating` — no notification call. The structural gap was immediately obvious from reading the two functions together.

**Root cause:** `rate_song()` in `notification_service.py` saves the `Rating` record and commits, but contains no call to `create_notification()`. The parallel function `add_to_playlist()` in the same file correctly notifies the song's sharer after its DB write. The notification step was simply never written into `rate_song()`.

**Fix and side-effect check:** Added a `create_notification()` call after `db.session.commit()` in `rate_song()`, guarded by `if song.shared_by != user_id` (the same guard `add_to_playlist` uses to avoid self-notifications). The only other code that touches `Notification` creation is `create_notification()` itself and `add_to_playlist()` — both unmodified. Checked `get_notifications()` and `mark_as_read()` — read-only and unaffected. Verified: rating nova's own song does not trigger a notification (self-guard holds); rating another user's song creates exactly one `song_rated` notification for the sharer.

---

### Issue 5 — Last song in playlist never shows up (`playlist_service.py`)

**How I reproduced it:** Queried "Late Night Vibes" playlist via `get_playlist_songs`. The `playlist_entries` table shows 7 songs at positions 1–7. The function returned 6 songs — "Free Throws" by Hoop Dreams (position 7) was absent. Confirmed it is in `playlist_entries` with `position=7`; the query fetches it correctly but it never reaches the caller.

**How I found the root cause:** Issue said "last song never shows up" → README pointed to `playlist_service.py`. Call chain: `GET /playlists/<id>/songs` → `routes/playlists.py` → `playlist_service.get_playlist_songs()`. Read `get_playlist_songs()` top-down. The SQL query is correct — `JOIN playlist_entries`, filter by `playlist_id`, `ORDER BY position ASC`. But the return statement is `return [song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice is the only thing between the correct query result and the response. No conditional, no edge case — it unconditionally drops the last element every time.

**Root cause:** `get_playlist_songs()` in `playlist_service.py:66` returns `songs[:-1]` instead of `songs`. Python's `[:-1]` slice drops the last element of a list. For any playlist with N songs, this returns N-1 songs, silently omitting the one at the highest position. This affects every playlist regardless of size.

**Fix and side-effect check:** Changed `songs[:-1]` to `songs`. Checked the only other path that reads playlist songs — `notification_service.add_to_playlist()` imports `get_playlist_songs` to check for duplicates before adding. With the fix it now sees the full song list, which is correct (previously it would miss the last song when checking for duplicates, potentially allowing the same song to be added again). Ran all three playlist tests — `test_playlist_returns_all_songs`, `test_playlist_returns_songs_in_order`, `test_empty_playlist_returns_empty_list` — all pass.

---

## Other bugs also fixed

### Issue 2 — Friends Listening Now shows yesterday's listeners (`feed_service.py`)

`RECENT_THRESHOLD = timedelta(hours=24)` was too broad. Changed to `timedelta(minutes=30)`.

### Issue 3 — Duplicate songs in search results (`search_service.py`)

`search_songs` joined `song_tags` unnecessarily (the filter only uses title/artist). For a song with N tags, the SQL produces N rows with the same song ID. Added `.distinct()`. Note: SQLAlchemy 2.0's identity map masks the symptom at the ORM level, but the raw SQL duplication is real (verified: 3 raw rows for "Crown Heights Anthem" which has 3 tags).
