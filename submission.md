# Mixtape Bug Hunt — Submission

## AI Usage

<!-- TODO: Fill this in as we go, and finalize in Milestone 4.
Describe specific instances of AI use during navigation/debugging —
what you asked, what it helped you understand, and at least one place
where you verified or corrected something yourself. So far:
- Used AI to help build the initial codebase map from app.py, models.py,
  routes/, and services/ before touching any bug.
- Used AI to identify which files were relevant to each of the 5 issues
  and to point out specific lines worth investigating, but verified every
  root cause myself by reading the code and reproducing the bug against
  the running app (e.g. for Issue #5, confirmed the [:-1] slice was the
  cause by reading get_playlist_songs() directly and re-testing after
  the fix). -->

## Codebase Map

### Main files and their roles

- **app.py** — Flask application factory. Creates the Flask app, configures
  SQLAlchemy (SQLite by default via `mixtape.db`), registers four blueprints
  (`songs`, `playlists`, `users`, `feed`), and calls `db.create_all()`.

- **models.py** — SQLAlchemy models: `User`, `Tag`, `Song`, `ListeningEvent`,
  `Rating`, `Playlist`, `Notification`, plus three association tables:
  `friendships` (symmetric many-to-many), `song_tags` (many-to-many), and
  `playlist_entries` (many-to-many WITH an explicit `position` column, so
  playlist order is stored explicitly rather than inferred from insertion
  order).

- **routes/songs.py** — `/songs/search`, `/songs/<id>`, `/songs/<id>/rate`,
  `/songs/<id>/listen`. Delegates to `search_service`, `notification_service`
  (for rating), and `streak_service` (for listen events).

- **routes/playlists.py** — `/playlists/` (create), `/playlists/<id>`,
  `/playlists/<id>/songs` (GET and POST). Adding a song to a playlist is
  handled by `notification_service.add_to_playlist()`, not `playlist_service`
  — the playlist mutation and the notification side-effect live together in
  one function.

- **routes/users.py** — `/users/<id>`, `/users/<id>/streak`,
  `/users/<id>/notifications`, `/users/notifications/<id>/read`.

- **routes/feed.py** — `/feed/<user_id>/listening-now`,
  `/feed/<user_id>/activity`.

- **services/streak_service.py** — Owns `record_listening_event()` (creates a
  `ListeningEvent`, then calls `update_listening_streak()`) and `get_streak()`.
  Streak rule: same day = no change, exactly 1 day gap = increment, more than
  1 day gap = reset to 1.

- **services/feed_service.py** — `get_friends_listening_now()` (recent-only,
  gated by `RECENT_THRESHOLD`) vs. `get_activity_feed()` (all-time, capped by
  `limit`). Both query off the `friendships` relationship.

- **services/search_service.py** — `search_songs()` does a case-insensitive
  `ilike` match against title/artist, joined against the `song_tags` table.

- **services/notification_service.py** — `create_notification()` is the
  single low-level writer. Two higher-level functions call it:
  `add_to_playlist()` (adds the song to the playlist AND notifies the sharer)
  and `rate_song()` (saves the rating — and is supposed to notify the sharer
  the same way).

- **services/playlist_service.py** — `create_playlist()`, `get_playlist()`,
  `get_playlist_songs()` (ordered by `playlist_entries.position`),
  `get_user_playlists()`.

- **seed_data.py** — Seeds 5 users with friendships, 25 songs deliberately
  split into 0-tag / 1-tag / 3+-tag groups, listening events split into
  "recent" (10–20 min ago) and "older" (2–58 hours ago) buckets, 3 playlists
  of 5–7 songs, and one working playlist-notification.

### Data flow — a friend rates your shared song

1. Client calls `POST /songs/<song_id>/rate` with `user_id` and `score`.
2. `routes/songs.py::rate()` validates input, calls
   `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates the score (1–5), looks up the `Song` and rating
   `User`, then either updates an existing `Rating` row (unique on `user_id` +
   `song_id`) or creates a new one, and commits.
4. The route returns the `Rating` as JSON.

### Data flow — a friend adds your shared song to a playlist

1. Client calls `POST /playlists/<playlist_id>/songs` with `song_id`,
   `added_by`.
2. `routes/playlists.py::add_song()` calls
   `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
3. `add_to_playlist()` looks up the `Song`, the adding `User`, and the
   `Playlist`; appends the song to `playlist.songs` if not already present;
   commits; then — if the adder isn't the original sharer — calls
   `create_notification()` to notify `song.shared_by`.

### Pattern noticed

Every route delegates immediately to a service function — routes do input
parsing and status codes, services do all business logic and DB access. Two
different "write" actions that should both notify the sharer (rating a song,
adding it to a playlist) are handled in the *same* service file
(`notification_service.py`), but only one of the two currently follows
through on the notification step.

## Root Cause Analysis

### Issue #5: Last playlist song never shows

**How I reproduced it:** Queried `Playlist.query.all()` and a helper count
against `playlist_entries` in a Flask shell to get the true seeded song count
for each of the 3 playlists (all seeded with 7 songs). Then hit
`GET /playlists/<id>/songs` for "Late Night Vibes" and got back `"count": 7`
seeded vs. `"count": 6` returned, with the last song ("Free Throws" by
position) missing from the response.

**How I found the root cause:** Opened `services/playlist_service.py` and
read `get_playlist_songs()`. The SQL query itself was correct — it orders
songs by `playlist_entries.c.position` ascending, so the full ordered list is
right. The bug was in the very last line, the return statement, not the
query.

**The root cause:** `get_playlist_songs()` returns
`[song.to_dict() for song in songs[:-1]]`. The `songs` list is already
correctly ordered by position, but the `[:-1]` slice drops the last element
of that list before it's serialized — so the highest-position song in every
playlist (i.e., whichever song was added last) is silently excluded from
every response, regardless of playlist size.

**My fix and side-effect check:** Changed the return statement to
`[song.to_dict() for song in songs]`, removing the slice entirely so the full
ordered list is returned. Verified against all 3 seeded playlists
("Late Night Vibes", "Friday Energy", "Study Mode") — all now return the full
7 songs each, with the correct song in the final position. Also reviewed
`get_playlist()` and `get_user_playlists()` in the same file; neither touches
the songs list or reuses `get_playlist_songs()`, so they were unaffected by
both the bug and the fix.

<!-- TODO: Add entries for Issues #4, #1, #2, #3 as we fix them. -->

## Regression Test

<!-- TODO: Write after all required fixes are done — reference the test
file and explain what behavior it verifies and why it would have failed
against the buggy code. -->

## Commit History

<!-- TODO: Paste screenshot of `git log --oneline` on bugfix/mixtape here
once all fixes are committed. -->