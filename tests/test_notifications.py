"""
tests/test_notifications.py — Mixtape

Tests for notification creation logic.
"""

import pytest
from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_song(app):
    """Create a song sharer and a song they shared."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        db.session.add(sharer)
        db.session.flush()

        song = Song(title="Test Track", artist="Test Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "song": song}


def test_rating_notifies_the_sharer(app, sharer_and_song):
    """
    Rating a song shared by someone else should create a notification
    for the original sharer. Bug caused this to never happen.
    """
    with app.app_context():
        sharer_id = sharer_and_song["sharer"].id
        song_id = sharer_and_song["song"].id

        rater = User(username="rater", email="rater@example.com")
        db.session.add(rater)
        db.session.commit()

        rate_song(rater.id, song_id, 5)

        notifications = Notification.query.filter_by(user_id=sharer_id).all()
        assert len(notifications) == 1  # Bug caused this to be 0
        assert notifications[0].notification_type == "song_rated"


def test_self_rating_does_not_notify(app, sharer_and_song):
    """Rating your own shared song should not create a notification."""
    with app.app_context():
        sharer_id = sharer_and_song["sharer"].id
        song_id = sharer_and_song["song"].id

        rate_song(sharer_id, song_id, 4)

        notifications = Notification.query.filter_by(user_id=sharer_id).all()
        assert notifications == []