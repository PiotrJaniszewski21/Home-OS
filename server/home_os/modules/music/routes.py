from functools import wraps
import gzip
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx

from flask import Response, abort, after_this_request, current_app, jsonify, render_template, request, send_file, stream_with_context, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from home_os.modules.music import music_bp
from home_os.extensions import db
from home_os.models import (
    MusicListen,
    MusicPlaybackMetric,
    MusicPlaylist,
    MusicPlaylistTrack,
    MusicSavedAlbum,
)
from home_os.services.home_music import HomeMusicError, home_music_service
from home_os.services.live_radio import LiveRadioError, live_radio_service
from home_os.services.rate_limiter import music_limiter


PLAYBACK_TICKET_MAX_AGE = 300
RADIO_TICKET_MAX_AGE = 3600
PLAYBACK_METRIC_SCENARIOS = {
    "selection",
    "playlist_selection",
    "next",
    "previous",
    "autoplay",
}
PLAYBACK_METRIC_SOURCES = {
    "downloaded",
    "device_cache",
    "starter_cache",
    "server_cache",
    "provider_stream",
    "fallback_proxy",
}


@music_bp.before_request
def require_music_permission():
    if current_user.is_authenticated and not current_user.has_permission("media"):
        if request.path.startswith("/api/") or request.path == "/proxy-stream":
            return jsonify({"ok": False, "error": "Media access required"}), 403
        abort(403)


@music_bp.after_request
def compress_music_json(response):
    if (
        not 200 <= response.status_code < 300
        or response.direct_passthrough
        or response.mimetype != "application/json"
        or response.headers.get("Content-Encoding")
        or "gzip" not in request.headers.get("Accept-Encoding", "").lower()
    ):
        return response
    payload = response.get_data()
    if len(payload) < 1024:
        return response
    response.set_data(gzip.compress(payload, compresslevel=4, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = len(response.get_data())
    response.headers.add("Vary", "Accept-Encoding")
    return response


@music_bp.route("/music")
@login_required
def music_view():
    return render_template("music/music.html")


def _enforce_rate_limit(category, maximum):
    identity = current_user.id if current_user.is_authenticated else request.remote_addr
    key = f"music:{category}:{identity}"
    if music_limiter.is_limited(key, max_attempts=maximum):
        return jsonify({"ok": False, "error": "Too many music requests"}), 429
    music_limiter.record(key)
    return None


def _optional_metric_milliseconds(data, name):
    value = data.get(name)
    if value is None:
        return None
    value = int(value)
    if not 0 <= value <= 300_000:
        raise ValueError(f"{name} is outside the accepted range")
    return value


def _ticket_serializer():
    return URLSafeTimedSerializer(
        current_app.secret_key,
        salt="home-music-playback-v1",
    )


def _create_playback_ticket(video_id):
    return _ticket_serializer().dumps({
        "track_id": video_id,
        "user_id": current_user.id,
        "auth_version": current_user.auth_version,
    })


def _valid_playback_ticket(ticket, video_id):
    if not ticket:
        return False
    try:
        payload = _ticket_serializer().loads(
            ticket,
            max_age=PLAYBACK_TICKET_MAX_AGE,
        )
    except (BadSignature, SignatureExpired):
        return False
    if payload.get("track_id") != video_id:
        return False

    from home_os.extensions import db
    from home_os.models import User

    user = db.session.get(User, payload.get("user_id"))
    return bool(
        user
        and user.is_active
        and user.has_permission("media")
        and user.auth_version == payload.get("auth_version")
    )


def _create_radio_ticket(station):
    return _ticket_serializer().dumps({
        "station_id": station["id"],
        "stream_url": station["stream_url"],
        "user_id": current_user.id,
        "auth_version": current_user.auth_version,
    })


def _radio_stream_from_ticket(ticket):
    if not ticket:
        return None
    try:
        payload = _ticket_serializer().loads(
            ticket,
            max_age=RADIO_TICKET_MAX_AGE,
        )
    except (BadSignature, SignatureExpired):
        return None

    from home_os.extensions import db
    from home_os.models import User

    user = db.session.get(User, payload.get("user_id"))
    stream_url = payload.get("stream_url")
    if not (
        user
        and user.is_active
        and user.has_permission("media")
        and user.auth_version == payload.get("auth_version")
        and live_radio_service.is_public_stream_url(stream_url)
    ):
        return None
    return stream_url


def _radio_stream_url(station):
    forwarded_scheme = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    scheme = forwarded_scheme if forwarded_scheme in {"http", "https"} else request.scheme
    return url_for(
        "music.radio_stream",
        ticket=_create_radio_ticket(station),
        _external=True,
        _scheme=scheme,
    )


def playback_access_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        video_id = request.args.get("id", "")
        if current_user.is_authenticated or _valid_playback_ticket(
            request.args.get("ticket", ""),
            video_id,
        ):
            return function(*args, **kwargs)
        return jsonify({"ok": False, "error": "Authentication required"}), 401

    return decorated


def _search_response(legacy=False):
    limited = _enforce_rate_limit("search", 30)
    if limited:
        return limited
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", 20))
        tracks = home_music_service.search(query, limit=limit)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic search failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502

    response = jsonify(tracks if legacy else {"ok": True, "data": tracks})
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@music_bp.route("/api/music/search")
@login_required
def search():
    return _search_response()


@music_bp.route("/api/music/genres")
@login_required
def genres():
    response = jsonify({
        "ok": True,
        "data": home_music_service.genres(),
    })
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@music_bp.route("/api/music/search/smart")
@login_required
def smart_search():
    limited = _enforce_rate_limit("smart-search", 30)
    if limited:
        return limited
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", 25))
        genre = home_music_service.resolve_genre(query)
        if genre is None:
            tracks = home_music_service.search(query, limit=limit)
            recent_releases = []
            classics = []
            hot_artists = []
        else:
            page = home_music_service.genre_page(
                genre,
                limit=limit,
            )
            tracks = page["popular"]
            recent_releases = page["recent_releases"]
            classics = page["classics"]
            hot_artists = page["hot_artists"]
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic smart search failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502

    response = jsonify({
        "ok": True,
        "data": {
            "tracks": tracks,
            "genre": genre,
            "recent_releases": recent_releases,
            "classics": classics,
            "hot_artists": hot_artists,
        },
    })
    response.headers["Cache-Control"] = "private, max-age=300, stale-if-error=86400"
    return response


@music_bp.route("/api/music/search/artists")
@login_required
def search_artists():
    limited = _enforce_rate_limit("artist-search", 30)
    if limited:
        return limited
    try:
        artists = home_music_service.search_artists(
            request.args.get("q", ""),
            limit=request.args.get("limit", 8),
        )
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic artist search failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    response = jsonify({"ok": True, "data": artists})
    response.headers["Cache-Control"] = "private, max-age=900, stale-if-error=86400"
    return response


@music_bp.route("/api/music/search/albums")
@login_required
def search_albums():
    limited = _enforce_rate_limit("album-search", 30)
    if limited:
        return limited
    try:
        albums = home_music_service.search_albums(
            request.args.get("q", ""),
            limit=request.args.get("limit", 12),
        )
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic album search failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    response = jsonify({"ok": True, "data": albums})
    response.headers["Cache-Control"] = "private, max-age=900, stale-if-error=86400"
    return response


@music_bp.route("/api/music/search/unified")
@login_required
def unified_search():
    limited = _enforce_rate_limit("unified-search", 60)
    if limited:
        return limited
    try:
        data = home_music_service.unified_search(
            request.args.get("q", ""),
            limit=request.args.get("limit", 20),
        )
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic unified search failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    response = jsonify({"ok": True, "data": data})
    response.headers["Cache-Control"] = "private, max-age=300, stale-if-error=86400"
    return response


@music_bp.route("/api/music/artists/<browse_id>")
@login_required
def artist_detail(browse_id):
    limited = _enforce_rate_limit("artist-detail", 40)
    if limited:
        return limited
    try:
        artist = home_music_service.artist(browse_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic artist details failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    response = jsonify({"ok": True, "data": artist})
    response.headers["Cache-Control"] = "private, max-age=21600, stale-if-error=604800"
    return response


@music_bp.route("/api/music/albums/<browse_id>")
@login_required
def album_detail(browse_id):
    limited = _enforce_rate_limit("album-detail", 40)
    if limited:
        return limited
    try:
        album = home_music_service.album(browse_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic album details failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    response = jsonify({"ok": True, "data": album})
    response.headers["Cache-Control"] = "private, max-age=21600, stale-if-error=604800"
    return response


def _saved_album_for_current_user(album_id):
    return MusicSavedAlbum.query.filter_by(
        user_id=current_user.id,
        album_id=album_id,
    ).first()


@music_bp.route("/api/music/albums/library")
@login_required
def saved_albums():
    albums = (
        MusicSavedAlbum.query
        .filter_by(user_id=current_user.id)
        .order_by(MusicSavedAlbum.saved_at.desc(), MusicSavedAlbum.id.desc())
        .all()
    )
    return jsonify({"ok": True, "data": [album.to_dict() for album in albums]})


@music_bp.route("/api/music/albums/library/<browse_id>", methods=["PUT", "DELETE"])
@login_required
def update_saved_album(browse_id):
    try:
        browse_id = home_music_service.validate_browse_id(browse_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    album = _saved_album_for_current_user(browse_id)
    if request.method == "DELETE":
        if album is not None:
            db.session.delete(album)
            db.session.commit()
        return jsonify({"ok": True, "data": {"deleted": True}})

    data = request.get_json(silent=True) or {}
    title = " ".join(str(data.get("title") or "").split())[:300]
    artist = " ".join(str(data.get("artist") or "").split())[:300]
    if not title:
        return jsonify({"ok": False, "error": "Album title is required"}), 400
    if album is None:
        album = MusicSavedAlbum(user_id=current_user.id, album_id=browse_id)
        db.session.add(album)
    album.title = title
    album.artist = artist
    album.thumbnail = str(data.get("thumbnail") or "")[:2048]
    album.year = str(data.get("year") or "")[:20]
    album.album_type = " ".join(str(data.get("type") or "Album").split())[:40] or "Album"
    db.session.commit()
    return jsonify({"ok": True, "data": album.to_dict()})


@music_bp.route("/search")
@login_required
def legacy_search():
    return _search_response(legacy=True)


_playback_ticket_cache = {}


@music_bp.route("/api/music/playback-url")
@login_required
def playback_url():
    limited = _enforce_rate_limit("ticket", 240)
    if limited:
        return limited
    try:
        video_id = home_music_service.validate_video_id(request.args.get("id", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    now = time.time()
    if video_id in _playback_ticket_cache:
        cached_data, expires_at = _playback_ticket_cache[video_id]
        if now < expires_at:
            ticket = _create_playback_ticket(video_id)
            cached_data["path"] = url_for("music.proxy_stream", id=video_id, ticket=ticket)
            return jsonify({"ok": True, "data": cached_data})

    ticket = _create_playback_ticket(video_id)
    path = url_for(
        "music.proxy_stream",
        id=video_id,
        ticket=ticket,
    )
    listen = MusicListen.query.filter_by(
        user_id=current_user.id,
        track_id=video_id,
    ).first()
    cached_path = home_music_service.cached_audio_path(video_id)
    if cached_path is not None:
        duration_seconds = listen.duration_seconds if listen else None
        if duration_seconds is None:
            duration_seconds = home_music_service.cached_audio_duration(video_id)
        direct_url = None
        source_expires_at = None
    else:
        try:
            stream = home_music_service.stream_details(video_id)
        except HomeMusicError as error:
            current_app.logger.warning(
                "HomeMusic playback preparation failed: %s",
                error,
            )
            return jsonify({"ok": False, "error": str(error)}), 502
        duration_seconds = stream.duration_seconds
        direct_url = stream.url
        source_expires_at = getattr(stream, "expires_at", None)
        home_music_service.schedule_audio_cache(video_id)
    if listen is not None and listen.duration_seconds is None and duration_seconds:
        listen.duration_seconds = round(duration_seconds)
        db.session.commit()
    result_data = {
        "path": path,
        "direct_url": direct_url,
        "expires_in": PLAYBACK_TICKET_MAX_AGE,
        "duration_seconds": duration_seconds,
        "source_expires_at": source_expires_at,
        "cache_hit": cached_path is not None,
    }
    if direct_url:
        _playback_ticket_cache[video_id] = (dict(result_data), now + 180)
    return jsonify({
        "ok": True,
        "data": result_data,
    })


@music_bp.route("/api/music/cache/prepare", methods=["POST"])
@login_required
def prepare_audio_cache():
    limited = _enforce_rate_limit("cache-prepare", 120)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    track_ids = data.get("track_ids")
    if not isinstance(track_ids, list):
        return jsonify({"ok": False, "error": "track_ids must be a list"}), 400
    result = home_music_service.schedule_audio_cache_many(track_ids, limit=20)
    return jsonify({"ok": True, "data": result}), 202


def _clean_track_payload(data):
    video_id = home_music_service.validate_video_id(data.get("id", ""))
    title = " ".join(str(data.get("title") or "").split())[:300]
    artist = " ".join(str(data.get("artist") or "").split())[:300]
    thumbnail = str(data.get("thumbnail") or "")[:2048]
    if not title or not artist:
        raise ValueError("Track title and artist are required")
    duration = data.get("duration_seconds")
    if duration in (None, ""):
        duration = None
    else:
        duration = max(0, min(int(duration), 24 * 60 * 60))
    played_seconds = max(0, min(int(data.get("played_seconds") or 0), 24 * 60 * 60))
    return video_id, title, artist, thumbnail, duration, played_seconds


@music_bp.route("/api/music/history")
@login_required
def history():
    listens = (
        MusicListen.query
        .filter_by(user_id=current_user.id)
        .order_by(MusicListen.last_played_at.desc())
        .limit(50)
        .all()
    )
    return jsonify({"ok": True, "data": [listen.to_track_dict() for listen in listens]})


@music_bp.route("/api/music/playback-metrics", methods=["POST"])
@login_required
def record_playback_metric():
    limited = _enforce_rate_limit("playback-metrics", 600)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    try:
        event_id = str(UUID(str(data.get("event_id", ""))))
        track_id = home_music_service.validate_video_id(data.get("track_id", ""))
        scenario = str(data.get("scenario", ""))
        source_kind = str(data.get("source_kind", ""))
        if scenario not in PLAYBACK_METRIC_SCENARIOS:
            raise ValueError("Invalid playback scenario")
        if source_kind not in PLAYBACK_METRIC_SOURCES:
            raise ValueError("Invalid playback source")
        source_ready_ms = _optional_metric_milliseconds(
            data,
            "source_ready_ms",
        )
        audible_ms = _optional_metric_milliseconds(data, "audible_ms")
        success = bool(data.get("success"))
        if success and audible_ms is None:
            raise ValueError("Successful playback requires audible_ms")
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    existing = MusicPlaybackMetric.query.filter_by(event_id=event_id).first()
    if existing is not None:
        return jsonify({"ok": True, "data": {"recorded": False}})

    metric = MusicPlaybackMetric(
        event_id=event_id,
        user_id=current_user.id,
        track_id=track_id,
        scenario=scenario,
        source_kind=source_kind,
        source_ready_ms=source_ready_ms,
        audible_ms=audible_ms,
        success=success,
        fallback_used=bool(data.get("fallback_used")),
        app_version=str(data.get("app_version") or "")[:32],
        os_version=str(data.get("os_version") or "")[:64],
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    MusicPlaybackMetric.query.filter(
        MusicPlaybackMetric.recorded_at < cutoff
    ).delete(synchronize_session=False)
    db.session.add(metric)
    db.session.commit()
    return jsonify({"ok": True, "data": {"recorded": True}}), 201


@music_bp.route("/api/music/cache/candidates")
@login_required
def cache_candidates():
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    listens = (
        MusicListen.query
        .filter_by(user_id=current_user.id)
        .order_by(
            MusicListen.liked.desc(),
            MusicListen.last_played_at.desc(),
            MusicListen.play_count.desc(),
            MusicListen.completed_count.desc(),
        )
        .limit(200)
        .all()
    )
    candidates = [
        listen
        for listen in listens
        if (
            listen.liked
            or listen.last_played_at.replace(tzinfo=timezone.utc) >= cutoff
            or listen.play_count >= 2
            or listen.completed_count >= 2
        )
    ][:60]
    return jsonify({
        "ok": True,
        "data": [listen.to_track_dict() for listen in candidates],
    })


@music_bp.route("/api/music/library")
@login_required
def library():
    listens = (
        MusicListen.query
        .filter_by(user_id=current_user.id, liked=True)
        .order_by(MusicListen.last_played_at.desc())
        .limit(200)
        .all()
    )
    return jsonify({"ok": True, "data": [listen.to_track_dict() for listen in listens]})


@music_bp.route("/api/music/history", methods=["POST"])
@login_required
def record_history():
    data = request.get_json(silent=True) or {}
    try:
        video_id, title, artist, thumbnail, duration, played_seconds = _clean_track_payload(data)
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    listen = MusicListen.query.filter_by(
        user_id=current_user.id,
        track_id=video_id,
    ).first()
    now = datetime.now(timezone.utc)
    completed = bool(data.get("completed"))
    is_new_listen = listen is None
    if is_new_listen:
        listen = MusicListen(
            user_id=current_user.id,
            track_id=video_id,
            play_count=1,
            total_play_seconds=0,
            completed_count=0,
        )
        db.session.add(listen)
    elif not completed:
        listen.play_count += 1
    listen.title = title
    listen.artist = artist
    listen.thumbnail = thumbnail
    if duration is not None:
        listen.duration_seconds = duration
    if completed and not is_new_listen:
        played_seconds = max(0, played_seconds - 30)
    listen.total_play_seconds = (listen.total_play_seconds or 0) + played_seconds
    listen.completed_count = (listen.completed_count or 0) + int(completed)
    listen.last_played_at = now
    db.session.commit()
    return jsonify({"ok": True, "data": listen.to_track_dict()})


@music_bp.route("/api/music/library/<video_id>", methods=["PUT"])
@login_required
def update_library(video_id):
    try:
        video_id = home_music_service.validate_video_id(video_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    listen = MusicListen.query.filter_by(
        user_id=current_user.id,
        track_id=video_id,
    ).first()
    data = request.get_json(silent=True) or {}
    if listen is None:
        try:
            _, title, artist, thumbnail, duration, _ = _clean_track_payload({
                **data,
                "id": video_id,
            })
        except (TypeError, ValueError) as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        listen = MusicListen(
            user_id=current_user.id,
            track_id=video_id,
            title=title,
            artist=artist,
            thumbnail=thumbnail,
            duration_seconds=duration,
            play_count=0,
            total_play_seconds=0,
            completed_count=0,
        )
        db.session.add(listen)
    listen.liked = bool(data.get("liked"))
    db.session.commit()
    return jsonify({"ok": True, "data": listen.to_track_dict()})


def _playlist_for_current_user(playlist_id):
    return MusicPlaylist.query.filter_by(id=playlist_id, user_id=current_user.id).first()


def _clean_playlist_payload(data, require_name=True):
    name = " ".join(str(data.get("name") or "").split())[:120]
    description = " ".join(str(data.get("description") or "").split())[:500]
    if require_name and not name:
        raise ValueError("Playlist name is required")
    return name, description


@music_bp.route("/api/music/playlists", methods=["GET", "POST"])
@login_required
def playlists():
    if request.method == "GET":
        records = (
            MusicPlaylist.query
            .filter_by(user_id=current_user.id)
            .order_by(MusicPlaylist.updated_at.desc(), MusicPlaylist.id.desc())
            .all()
        )
        return jsonify({"ok": True, "data": [item.to_dict() for item in records]})

    data = request.get_json(silent=True) or {}
    try:
        name, description = _clean_playlist_payload(data)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    playlist = MusicPlaylist(
        user_id=current_user.id,
        name=name,
        description=description,
    )
    db.session.add(playlist)
    db.session.commit()
    return jsonify({"ok": True, "data": playlist.to_dict()}), 201


@music_bp.route("/api/music/playlists/<int:playlist_id>", methods=["GET", "PATCH", "DELETE"])
@login_required
def playlist_detail(playlist_id):
    playlist = _playlist_for_current_user(playlist_id)
    if playlist is None:
        return jsonify({"ok": False, "error": "Playlist not found"}), 404
    if request.method == "GET":
        return jsonify({"ok": True, "data": playlist.to_dict()})
    if request.method == "DELETE":
        db.session.delete(playlist)
        db.session.commit()
        return jsonify({"ok": True, "data": {"deleted": True}})

    data = request.get_json(silent=True) or {}
    try:
        name, description = _clean_playlist_payload(data, require_name=False)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    if "name" in data:
        if not name:
            return jsonify({"ok": False, "error": "Playlist name is required"}), 400
        playlist.name = name
    if "description" in data:
        playlist.description = description
    db.session.commit()
    return jsonify({"ok": True, "data": playlist.to_dict()})


@music_bp.route("/api/music/playlists/<int:playlist_id>/tracks", methods=["POST"])
@login_required
def add_playlist_track(playlist_id):
    playlist = _playlist_for_current_user(playlist_id)
    if playlist is None:
        return jsonify({"ok": False, "error": "Playlist not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        video_id, title, artist, thumbnail, duration, _ = _clean_track_payload(data)
        if duration is None or duration <= 0:
            try:
                details = home_music_service.stream_details(video_id)
                if details.duration_seconds:
                    duration = int(details.duration_seconds)
            except Exception:
                pass
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    existing = MusicPlaylistTrack.query.filter_by(
        playlist_id=playlist.id,
        track_id=video_id,
    ).first()
    if existing is None:
        existing = MusicPlaylistTrack(
            playlist_id=playlist.id,
            track_id=video_id,
            title=title,
            artist=artist,
            thumbnail=thumbnail,
            duration_seconds=duration,
            position=len(playlist.tracks),
        )
        db.session.add(existing)
    else:
        existing.title = title
        existing.artist = artist
        existing.thumbnail = thumbnail
        existing.duration_seconds = duration
    db.session.commit()
    return jsonify({"ok": True, "data": playlist.to_dict()})


@music_bp.route(
    "/api/music/playlists/<int:playlist_id>/tracks/<video_id>",
    methods=["DELETE"],
)
@login_required
def remove_playlist_track(playlist_id, video_id):
    playlist = _playlist_for_current_user(playlist_id)
    if playlist is None:
        return jsonify({"ok": False, "error": "Playlist not found"}), 404
    try:
        video_id = home_music_service.validate_video_id(video_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    track = MusicPlaylistTrack.query.filter_by(
        playlist_id=playlist.id,
        track_id=video_id,
    ).first()
    if track is None:
        return jsonify({"ok": False, "error": "Track not found in playlist"}), 404
    db.session.delete(track)
    db.session.commit()
    return jsonify({"ok": True, "data": playlist.to_dict()})


@music_bp.route("/api/music/recommendations")
@login_required
def recommendations():
    limited = _enforce_rate_limit("recommendations", 20)
    if limited:
        return limited
    listens = (
        MusicListen.query
        .filter_by(user_id=current_user.id)
        .order_by(
            MusicListen.liked.desc(),
            MusicListen.play_count.desc(),
            MusicListen.last_played_at.desc(),
        )
        .limit(20)
        .all()
    )
    if not listens:
        return jsonify({"ok": True, "data": [], "message": "Play a few songs to build your recommendations."})
    try:
        tracks = home_music_service.recommendations(
            [listen.track_id for listen in listens[:3]],
            exclude_ids=[listen.track_id for listen in listens],
            limit=request.args.get("limit", 20),
        )
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic recommendations failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    return jsonify({"ok": True, "data": tracks})


@music_bp.route("/api/music/home")
@login_required
def personalized_home():
    limited = _enforce_rate_limit("personalized-home", 30)
    if limited:
        return limited

    is_force_refresh = request.args.get("refresh") == "1"

    # 1. Recent listens (ordered by last_played_at)
    recent_listens = (
        MusicListen.query
        .filter_by(user_id=current_user.id)
        .order_by(MusicListen.last_played_at.desc())
        .limit(20)
        .all()
    )

    # 2. User playlist tracks (ordered by playlist updated_at)
    playlist_tracks = (
        MusicPlaylistTrack.query
        .join(MusicPlaylist, MusicPlaylistTrack.playlist_id == MusicPlaylist.id)
        .filter(MusicPlaylist.user_id == current_user.id)
        .order_by(MusicPlaylist.updated_at.desc(), MusicPlaylistTrack.id.desc())
        .limit(30)
        .all()
    )

    # 3. Liked tracks
    liked_listens = (
        MusicListen.query
        .filter_by(user_id=current_user.id, liked=True)
        .order_by(MusicListen.last_played_at.desc())
        .limit(20)
        .all()
    )

    if not recent_listens and not playlist_tracks and not liked_listens:
        return jsonify({
            "ok": True,
            "data": {"suggested_songs": [], "suggested_albums": [], "new_releases": []},
            "message": "Play a few songs or create a playlist to personalize Listen Now.",
        })

    # Assemble candidate seeds from all 3 sources
    raw_seed_candidates = []
    seen_seeds = set()

    for item in recent_listens[:10]:
        if item.track_id not in seen_seeds:
            seen_seeds.add(item.track_id)
            raw_seed_candidates.append(item.track_id)

    for item in playlist_tracks:
        if item.track_id not in seen_seeds:
            seen_seeds.add(item.track_id)
            raw_seed_candidates.append(item.track_id)

    for item in liked_listens:
        if item.track_id not in seen_seeds:
            seen_seeds.add(item.track_id)
            raw_seed_candidates.append(item.track_id)

    if is_force_refresh and len(raw_seed_candidates) > 5:
        import random
        top_seed = raw_seed_candidates[0]
        other_seeds = raw_seed_candidates[1:]
        seed_ids = [top_seed] + random.sample(other_seeds, min(4, len(other_seeds)))
    else:
        seed_ids = raw_seed_candidates[:5]

    # Collect artists across all sources
    recent_artists = []
    seen_artists = set()
    all_sources = list(recent_listens) + list(playlist_tracks) + list(liked_listens)
    if is_force_refresh:
        import random
        random.shuffle(all_sources)

    for item in all_sources:
        artist_name = getattr(item, "artist", None)
        if artist_name:
            primary_artist = artist_name.split(",")[0].strip()
            if primary_artist and primary_artist.casefold() not in seen_artists:
                seen_artists.add(primary_artist.casefold())
                recent_artists.append(primary_artist)

    exclude_ids = list(seen_seeds)

    try:
        payload = home_music_service.personalized_home(
            seed_ids,
            recent_artists[:5],
            exclude_ids=exclude_ids,
            cache_key=f"user:{current_user.id}",
            force_refresh=is_force_refresh,
        )
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic personalized home failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    return jsonify({"ok": True, "data": payload})


def _contextual_recommendations(seed_ids, exclude_ids, limit):
    limited = _enforce_rate_limit("contextual-recommendations", 60)
    if limited:
        return limited
    try:
        tracks = home_music_service.recommendations(
            seed_ids,
            exclude_ids=exclude_ids,
            limit=limit,
        )
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic contextual recommendations failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    return jsonify({"ok": True, "data": tracks})


@music_bp.route("/api/music/recommendations/context", methods=["POST"])
@login_required
def contextual_recommendations():
    data = request.get_json(silent=True) or {}
    seed_ids = data.get("seed_ids") or []
    exclude_ids = data.get("exclude_ids") or []
    if not isinstance(seed_ids, list) or not isinstance(exclude_ids, list):
        return jsonify({"ok": False, "error": "Recommendation identifiers must be lists"}), 400
    return _contextual_recommendations(
        seed_ids[-3:],
        exclude_ids[:250],
        data.get("limit", 15),
    )


@music_bp.route("/api/music/playlists/<int:playlist_id>/suggestions")
@login_required
def playlist_suggestions(playlist_id):
    playlist = _playlist_for_current_user(playlist_id)
    if playlist is None:
        return jsonify({"ok": False, "error": "Playlist not found"}), 404
    track_ids = [track.track_id for track in playlist.tracks]
    if not track_ids:
        return jsonify({"ok": True, "data": []})
    if len(track_ids) <= 3:
        seeds = track_ids
    else:
        seeds = [track_ids[0], track_ids[len(track_ids) // 2], track_ids[-1]]
    return _contextual_recommendations(
        seeds,
        track_ids,
        request.args.get("limit", 12),
    )


@music_bp.route("/api/music/radio/stations")
@login_required
def radio_stations():
    limited = _enforce_rate_limit("radio-directory", 40)
    if limited:
        return limited
    try:
        query = request.args.get("q", "").strip()
        if query:
            stations = live_radio_service.search(query, limit=request.args.get("limit", 40))
        else:
            stations = live_radio_service.featured(limit=request.args.get("limit", 40))
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except LiveRadioError as error:
        current_app.logger.warning("HomeMusic radio directory failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502
    compatible_stations = []
    for station in stations:
        if station.get("is_hls"):
            continue
        station_payload = dict(station)
        station_payload["stream_url"] = _radio_stream_url(station)
        compatible_stations.append(station_payload)
    return jsonify({"ok": True, "data": compatible_stations})


@music_bp.route("/api/music/radio/stream")
def radio_stream():
    limited = _enforce_rate_limit("radio-stream", 120)
    if limited:
        return limited
    stream_url = _radio_stream_from_ticket(request.args.get("ticket", ""))
    if stream_url is None:
        return jsonify({"ok": False, "error": "Authentication required"}), 401

    client = httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=15, read=None, write=15, pool=15),
        headers={
            "User-Agent": "HomeMusic/1.0",
            "Icy-MetaData": "0",
        },
    )
    try:
        upstream = client.send(
            client.build_request("GET", stream_url),
            stream=True,
        )
    except httpx.HTTPError as error:
        client.close()
        current_app.logger.warning("HomeMusic radio stream failed: %s", error)
        return jsonify({"ok": False, "error": "Radio station is temporarily unavailable"}), 502

    content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if upstream.status_code != 200 or not (
        content_type.startswith("audio/")
        or content_type == "application/octet-stream"
    ):
        upstream.close()
        client.close()
        return jsonify({"ok": False, "error": "Radio station returned an unsupported stream"}), 502

    def generate():
        try:
            yield from upstream.iter_bytes(chunk_size=32 * 1024)
        finally:
            upstream.close()
            client.close()

    return Response(
        stream_with_context(generate()),
        headers={
            "Cache-Control": "private, no-store",
            "Content-Type": upstream.headers.get("Content-Type", "audio/mpeg"),
            "X-Accel-Buffering": "no",
        },
        direct_passthrough=True,
    )


@music_bp.route("/api/music/stream")
@music_bp.route("/proxy-stream")
@playback_access_required
def proxy_stream():
    limited = _enforce_rate_limit("stream", 180)
    if limited:
        return limited
    try:
        video_id = home_music_service.validate_video_id(request.args.get("id", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    cached_path = home_music_service.cached_audio_path(video_id)
    if cached_path is not None:
        response = send_file(
            cached_path,
            mimetype="audio/mp4",
            conditional=True,
            max_age=0,
        )
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "private, max-age=86400, immutable"
        response.headers["X-HomeMusic-Cache"] = "hit"
        return response

    try:
        stream_url = home_music_service.stream_url(video_id)
    except HomeMusicError as error:
        current_app.logger.warning("HomeMusic stream extraction failed: %s", error)
        return jsonify({"ok": False, "error": str(error)}), 502

    upstream_headers = {}
    if request.headers.get("Range"):
        upstream_headers["Range"] = request.headers["Range"]
    client = httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=60))
    try:
        upstream = client.send(
            client.build_request("GET", stream_url, headers=upstream_headers),
            stream=True,
        )
    except httpx.HTTPError as error:
        client.close()
        current_app.logger.warning("HomeMusic upstream stream failed: %s", error)
        return jsonify({"ok": False, "error": "Audio stream is temporarily unavailable"}), 502

    if upstream.status_code not in (200, 206):
        upstream.close()
        client.close()
        home_music_service.invalidate_stream(video_id)
        try:
            refreshed_url = home_music_service.stream_url(video_id)
            client = httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(30, read=60),
            )
            upstream = client.send(
                client.build_request(
                    "GET",
                    refreshed_url,
                    headers=upstream_headers,
                ),
                stream=True,
            )
        except (HomeMusicError, httpx.HTTPError) as error:
            if "client" in locals():
                client.close()
            current_app.logger.warning(
                "HomeMusic stream refresh failed: %s",
                error,
            )
            return jsonify({
                "ok": False,
                "error": "Audio provider rejected the stream",
            }), 502
        if upstream.status_code not in (200, 206):
            upstream.close()
            client.close()
            return jsonify({
                "ok": False,
                "error": "Audio provider rejected the stream",
            }), 502

    def generate():
        try:
            yield from upstream.iter_bytes(chunk_size=64 * 1024)
        finally:
            upstream.close()
            client.close()

    response_headers = {
        "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
        "Cache-Control": "private, no-store",
        "Content-Type": upstream.headers.get("Content-Type", "audio/mp4"),
        "X-HomeMusic-Cache": "miss",
    }
    for name in ("Content-Length", "Content-Range", "ETag", "Last-Modified"):
        if upstream.headers.get(name):
            response_headers[name] = upstream.headers[name]
    return Response(
        stream_with_context(generate()),
        status=upstream.status_code,
        headers=response_headers,
        direct_passthrough=True,
    )


@music_bp.route("/api/music/download")
@login_required
def download_track():
    limited = _enforce_rate_limit("download", 120)
    if limited:
        return limited
    try:
        video_id = home_music_service.validate_video_id(request.args.get("id", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    cached_path = home_music_service.cached_audio_path(video_id)
    if cached_path is None and home_music_service.audio_cache_enabled:
        try:
            cached_path = home_music_service.cache_audio(video_id)
        except HomeMusicError as error:
            current_app.logger.warning(
                "HomeMusic cached download failed for %s: %s",
                video_id,
                error,
            )
            return jsonify({
                "ok": False,
                "error": "Audio download is temporarily unavailable",
            }), 502

    temporary_directory = None
    if cached_path is not None:
        prepared_path = cached_path
    else:
        temporary_directory = tempfile.mkdtemp(prefix="home-music-")
        try:
            prepared_path = home_music_service.download_audio(
                video_id,
                temporary_directory,
            )
        except HomeMusicError as error:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            current_app.logger.warning(
                "HomeMusic download failed for %s: %s",
                video_id,
                error,
            )
            return jsonify({
                "ok": False,
                "error": "Audio download is temporarily unavailable",
            }), 502

    if temporary_directory is not None:
        @after_this_request
        def remove_temporary_file(response):
            shutil.rmtree(temporary_directory, ignore_errors=True)
            return response

    response = send_file(
        prepared_path,
        mimetype="audio/mp4",
        as_attachment=True,
        download_name=f"{video_id}.m4a",
        conditional=True,
        max_age=0,
    )
    response.headers["X-HomeMusic-Cache"] = (
        "hit" if cached_path is not None else "disabled"
    )
    return response
