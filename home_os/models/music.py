from datetime import datetime, timezone

from home_os.extensions import db


class MusicListen(db.Model):
    __tablename__ = "music_listens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_id = db.Column(db.String(11), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    artist = db.Column(db.String(300), nullable=False)
    thumbnail = db.Column(db.String(2048), nullable=False, default="")
    duration_seconds = db.Column(db.Integer, nullable=True)
    play_count = db.Column(db.Integer, nullable=False, default=1)
    total_play_seconds = db.Column(db.Integer, nullable=False, default=0)
    completed_count = db.Column(db.Integer, nullable=False, default=0)
    liked = db.Column(db.Boolean, nullable=False, default=False)
    first_played_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_played_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    user = db.relationship(
        "User",
        backref=db.backref("music_listens", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "track_id", name="uq_music_listen_user_track"),
    )

    def to_track_dict(self):
        return {
            "id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "thumbnail": self.thumbnail,
            "duration_seconds": self.duration_seconds,
            "play_count": self.play_count,
            "liked": self.liked,
            "last_played_at": self.last_played_at.isoformat(),
        }


class MusicPlaylist(db.Model):
    __tablename__ = "music_playlists"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = db.relationship(
        "User",
        backref=db.backref("music_playlists", cascade="all, delete-orphan"),
    )
    tracks = db.relationship(
        "MusicPlaylistTrack",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="MusicPlaylistTrack.position, MusicPlaylistTrack.id",
    )

    def to_dict(self, include_tracks=True):
        payload = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "track_count": len(self.tracks),
            "artwork": [track.thumbnail for track in self.tracks[:4] if track.thumbnail],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_tracks:
            payload["tracks"] = [track.to_track_dict() for track in self.tracks]
        return payload


class MusicPlaylistTrack(db.Model):
    __tablename__ = "music_playlist_tracks"

    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(
        db.Integer,
        db.ForeignKey("music_playlists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_id = db.Column(db.String(11), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    artist = db.Column(db.String(300), nullable=False)
    thumbnail = db.Column(db.String(2048), nullable=False, default="")
    duration_seconds = db.Column(db.Integer, nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    added_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    playlist = db.relationship("MusicPlaylist", back_populates="tracks")

    __table_args__ = (
        db.UniqueConstraint("playlist_id", "track_id", name="uq_music_playlist_track"),
    )

    def to_track_dict(self):
        return {
            "id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "thumbnail": self.thumbnail,
            "duration_seconds": self.duration_seconds,
            "playlist_track_id": self.id,
        }


class MusicSavedAlbum(db.Model):
    __tablename__ = "music_saved_albums"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    album_id = db.Column(db.String(160), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    artist = db.Column(db.String(300), nullable=False, default="")
    thumbnail = db.Column(db.String(2048), nullable=False, default="")
    year = db.Column(db.String(20), nullable=False, default="")
    album_type = db.Column(db.String(40), nullable=False, default="Album")
    saved_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    user = db.relationship(
        "User",
        backref=db.backref("music_saved_albums", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "album_id", name="uq_music_saved_album_user_album"),
    )

    def to_dict(self):
        return {
            "id": self.album_id,
            "title": self.title,
            "artist": self.artist,
            "thumbnail": self.thumbnail,
            "year": self.year,
            "type": self.album_type,
            "saved_at": self.saved_at.isoformat(),
        }
