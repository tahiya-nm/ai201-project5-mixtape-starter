# Mixtape Bug Hunt — Submission

## AI Usage

I used Claude throughout this project for codebase navigation, tracing root
causes, and structuring my documentation — but every fix and every claim in
this submission was verified by me against the actual running app or test
suite before I accepted it.

**Navigation and codebase mapping:** Used AI to read through app.py,
models.py, routes/, and services/ and build an initial map of what each file
does and how the data flows for two features (rating a song, adding a song
to a playlist). I used this as a starting point but confirmed the structure
myself by reading the actual files.

**Locating likely root causes:** For all 5 issues, AI pointed me to specific
functions and lines worth investigating (e.g., the `[:-1]` slice in
`get_playlist_songs()`, the `.weekday() != 6` condition in
`update_listening_streak()`). In every case, I verified the actual behavior
myself — reproducing the bug against the running server or a Flask shell
before accepting any explanation, and re-testing after each fix.

**Where AI's first theory was wrong and I had to correct it:** For Issue #3
(duplicate search results), AI's initial theory was that the `outerjoin`
against `song_tags` would produce duplicate rows via the ORM. When I tested
this directly, it didn't reproduce — `search_songs()` returned only 1 result
for a 3-tag song, not 3. Rather than accept that, I ran the project's own
test suite (which had a test with a comment literally saying "bug causes it
to be 3") and then wrote diagnostic scripts to compare the raw SQL result
against the ORM's `.all()` result. That showed the raw SQL genuinely
returned 3 duplicate rows, but SQLAlchemy 2.0.51's legacy `Query.all()` was
silently deduplicating them — a version-specific behavior not guaranteed by
the `sqlalchemy>=2.0.0` constraint in requirements.txt. This was the most
involved investigation of the project and the AI's first hypothesis needed
real correction based on empirical testing, not just re-reading the code.

**A verification catch on my own fix:** After fixing and committing Issue #1
(streak logic), I later discovered via `git log --oneline -- services/
streak_service.py` that the fix commit never actually touched the file — the
Sunday bug was still present on disk despite having "passed" earlier manual
testing in a Flask shell session that had since ended. I caught this by
re-running the full test suite (`pytest tests/`) before finalizing, saw
`test_streak_increments_on_sunday` fail unexpectedly, traced it back with
`git log` and `git status`, reapplied the fix, and reverified with the test
suite (not just manual shell testing) before recommitting. This is why I
relied on the automated test suite as a final check rather than trusting
that a previously-verified manual test meant the fix was actually saved and
committed.

**Regression test:** For the stretch regression test, I initially considered
reusing tests that shipped with the starter repo (`test_playlists.py` and
`test_streaks.py` both already contained bug-specific tests with comments
revealing the intended bug). I chose instead to write a new test file,
`tests/test_notifications.py`, covering Issue #4, since no test file existed
for notification logic at all. I verified it was a genuine regression test
(not just a test that happens to pass) by temporarily commenting out my fix
in `rate_song()`, confirming the test failed (`assert 0 == 1`), then
restoring the fix and confirming it passed again.

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

### Issue #1: Listening streak keeps resetting

**How I reproduced it:** Since this bug is date-dependent, I controlled the
date directly in a Flask shell rather than relying on the actual current
date. Computed the most recent Sunday relative to today, set nova's
`last_listened_at` to the day before that Sunday (Saturday — a genuine
1-day gap), then called `update_listening_streak(nova, sunday)` directly with
her starting streak at 5. Expected the streak to increment to 6 (consecutive
day), but it reset to 1 instead.

**How I found the root cause:** Opened `services/streak_service.py` and read
the `elif` branch responsible for incrementing the streak:
`elif days_since_last == 1 and today.weekday() != 6:`. The `and` clause stood
out immediately, since streak continuation should only depend on how many
days passed, not on which day of the week it is. Confirmed via Python that
`.weekday()` returns 0 for Monday through 6 for Sunday, meaning
`!= 6` translates to "as long as today isn't Sunday."

**The root cause:** The increment condition requires both `days_since_last
== 1` AND `today.weekday() != 6`. On any Sunday, `today.weekday() != 6`
evaluates to `False`, so even when a user listened on truly consecutive days
(Saturday then Sunday), the `elif` fails and execution falls through to the
`else` branch, which unconditionally resets the streak to 1. This happens
every single week without exception, on every account, whenever the second
consecutive listening day happens to fall on a Sunday — matching the "keeps
resetting" bug report exactly. There's no legitimate reason day-of-week
should factor into whether a streak continues.

**My fix and side-effect check:** Removed the `and today.weekday() != 6`
condition, leaving `elif days_since_last == 1:` to increment based solely on
the day gap. Verified on both sides of the boundary condition: (1) a
Saturday→Sunday consecutive day now correctly increments 5 → 6; (2) a
Monday consecutive day (non-Sunday case) still increments 5 → 6 as before,
confirming the fix didn't disturb the working path; (3) a genuine 3-day gap
landing on a Sunday still correctly resets the streak to 1, confirming the
fix didn't overcorrect into "Sundays always increment regardless of gap";
(4) same-day listening still leaves the streak unchanged at 5, confirming the
no-op branch was untouched. I later caught, via a full pytest run, that my
first commit for this fix never actually saved the code change (see AI Usage
section) — reapplied the fix and reverified with the automated
`test_streak_increments_on_sunday` test before recommitting.

### Issue #2: Friends Listening Now shows people from yesterday

**How I reproduced it:** Hit `GET /feed/<nova_id>/listening-now` initially and
got an empty feed, since the original seed data's "recent" events (10-20 min
old at seed time) had aged out entirely by the time I tested. To get a
controlled reproduction, I used a Flask shell to manually insert a
`ListeningEvent` for darius (nova's friend) with `listened_at` set to exactly
2 hours before now. Re-querying the "listening now" endpoint showed darius in
the results with that 2-hour-old event — a feed meant to show who's
*currently* listening was showing someone from 2 hours ago.

**How I found the root cause:** Opened `services/feed_service.py` and read
`get_friends_listening_now()`. The query logic itself was correct — it
filters `ListeningEvent.listened_at >= cutoff` and dedupes to the most recent
event per friend. The problem was in how `cutoff` gets computed: it's
`datetime.now(timezone.utc) - RECENT_THRESHOLD`, and `RECENT_THRESHOLD` is
defined as a module-level constant at the top of the file.

**The root cause:** `RECENT_THRESHOLD = timedelta(hours=24)`. A 24-hour
window is far too generous for a feed called "listening now" — anyone who
listened to anything at any point in the last full day passes the filter and
is shown as if they're currently listening. This isn't a comparison bug or an
off-by-one; the threshold value itself was simply set to represent "today"
rather than "right now." The seed data's own comments support a much shorter
intended window — recent test events were seeded only 10–20 minutes old,
implying the feature was designed around a much tighter recency window than
what the constant enforced.

**My fix and side-effect check:** Changed `RECENT_THRESHOLD` from
`timedelta(hours=24)` to `timedelta(minutes=30)`. Verified: (1) the 2-hour-old
darius event now correctly disappears from "listening now" (count 1 → 0);
(2) a newly-added 10-minute-old event for darius correctly appears (count 0 →
1); (3) checked `get_activity_feed()` in the same file, which intentionally
has no time filter at all — confirmed it still returns the full history (10
events, including both the 2-hour-old and 10-minute-old ones), proving the
`RECENT_THRESHOLD` change is isolated to `get_friends_listening_now()` and
doesn't affect the unrelated activity feed function.

### Issue #3: The same song keeps showing up twice in search

**How I reproduced it:** Initially searched for a known 3-tag song
("Crown Heights Anthem") via `GET /songs/search?q=Crown` and got back
`count: 1` — no duplicate, contradicting my initial expectation. Ran the
project's own test suite (`pytest tests/test_search.py -v`) and all 5 tests
passed, including one whose own comment reads
`# Should be 1, bug causes it to be 3`. This told me the bug existed by
design but wasn't manifesting in my environment, so I needed to trace deeper
rather than trust a single black-box test.

**How I found the root cause:** Wrote a standalone script to run the exact
query from `search_songs()` directly and print `len(results)` — it returned
1, matching the earlier observation. To rule out ORM-level masking, I then
executed the query's raw compiled SQL directly via `db.session.execute()`
instead of going through the legacy `Query.all()` interface. That returned
**3** identical rows for the same song. This showed the join genuinely
produces 3 duplicate rows in the database result (one per row in
`song_tags`, since "Crown Heights Anthem" has 3 tags), but `db.session
.query(Song)....all()` was silently deduplicating those 3 identical
entities down to 1 before ever reaching `to_dict()`. Checked `pip show
sqlalchemy` (2.0.51 installed) against `requirements.txt`
(`sqlalchemy>=2.0.0`, an open floor with no ceiling) — confirming the
project's version constraint doesn't pin the exact point release the bug was
originally authored and tested against, and my installed version happens to
auto-deduplicate `Query.all()` results in a way an earlier one apparently
didn't.

**The root cause:** `search_songs()` performs
`.outerjoin(song_tags, Song.id == song_tags.c.song_id)` but never filters or
selects on anything from that table — tags are loaded separately via the
`Song.tags` relationship inside `to_dict()`. The join serves no functional
purpose in this query, and joining against a many-to-many association table
without a `.distinct()` guard is a classic source of duplicate rows: a song
with N tags produces N rows in the join result, one per tag. The underlying
SQL genuinely returns duplicate rows (confirmed directly), it's only masked
in this environment by SQLAlchemy 2.0.51's entity-loading behavior in the
legacy `Query` interface — behavior that isn't guaranteed across versions
and that the project's own test comment shows the bug's author didn't expect.
Relying on an un-pinned dependency's incidental behavior to hide a defect in
the query itself is not a safe fix — the defect (an unnecessary,
un-deduplicated join) was real regardless of whether it happened to be
visible in every environment.

**My fix and side-effect check:** Removed the `.outerjoin(song_tags, ...)`
clause entirely from `search_songs()`, since it was never used for filtering
or selection. Verified via the compiled SQL that the fixed query contains no
`JOIN` at all and returns exactly 1 raw row for "Crown Heights Anthem" —
eliminating the duplication risk structurally rather than relying on
ORM-version-specific deduplication. Confirmed the existing pytest suite
still passes (5/5). Also checked that tags still display correctly in the
search response (`["rap", "hip-hop", "boom bap"]`), confirming they load
correctly through the separate `Song.tags` relationship and the join was
truly unnecessary for that purpose.

### Issue #4: Missing rating notification

**How I reproduced it:** Checked `nova`'s notification count via
`GET /users/<nova_id>/notifications` (baseline: 1, from a seeded
playlist-add notification). Had `darius` rate one of nova's shared songs via
`POST /songs/<song_id>/rate`. The rating was saved successfully (returned a
valid `Rating` object), but re-checking nova's notifications afterward still
showed count 1 — no new notification was created.

**How I found the root cause:** Opened `services/notification_service.py`
and compared `add_to_playlist()` against `rate_song()`, since both represent
a friend interacting with a song someone else shared, and only one of them
was working. `add_to_playlist()` ends with a check
(`if song.shared_by != added_by_user_id:`) that calls `create_notification()`
to alert the original sharer. `rate_song()` performs the equivalent save
(update or create the `Rating`, then commit) but has no corresponding
notify step — it just returns after the commit.

**The root cause:** The notification step for ratings was never implemented.
This isn't a wrong condition or a typo — `rate_song()` is simply missing the
entire "notify the sharer" block that `add_to_playlist()` has. Structurally
the two functions do the same kind of thing (a friend acts on a shared song,
the sharer should be told), but only the playlist path was built out fully.

**My fix and side-effect check:** Added a notification block to `rate_song()`,
placed after the commit and modeled directly on the pattern in
`add_to_playlist()`: if `song.shared_by != user_id`, call
`create_notification()` with a `song_rated` type and a message including the
rater's username, song title, and score. Verified: (1) darius rating nova's
song now correctly produces a `song_rated` notification for nova
(count 1 → 2); (2) nova rating her own song does NOT produce a notification
(count stayed at 2), confirming the self-notification guard works the same
way it does in `add_to_playlist()`; (3) darius re-rating the same song
produces a second `song_rated` notification (count 2 → 3) — each rating
interaction generates its own notification, consistent with how
`add_to_playlist` behaves on repeated calls.

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

## Regression Test

Added `tests/test_notifications.py`, a new test file (no equivalent existed
in the starter repo for notification logic). The key test,
`test_rating_notifies_the_sharer`, rates a song shared by a different user
and asserts that exactly one `Notification` record is created for the
original sharer with `notification_type == "song_rated"`. A second test,
`test_self_rating_does_not_notify`, confirms rating your own song does not
create a notification.

I verified this is a genuine regression test, not just a test that happens
to pass: I temporarily commented out the notification block I added to
`rate_song()` (reverting to the original buggy behavior) and reran the test
— it failed with `assert 0 == 1`, confirming the sharer received zero
notifications under the buggy code, exactly matching Issue #4's reported
behavior. Restoring the fix made the test pass again. This test now runs as
part of the full suite (`pytest tests/`) and will catch any future
regression of this notification logic.

## Commit History

<img width="935" height="134" alt="Screenshot 2026-07-05 at 10 22 56 PM" src="https://github.com/user-attachments/assets/4d2941ac-537b-47de-a2f4-022fae67d96e" />
