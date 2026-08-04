import json
import os
from datetime import datetime, timedelta, timezone

from home_os.app import create_app
from home_os.models import MusicListen, MusicPlaylistTrack
from home_os.services.home_music import (
    DEFAULT_AUDIO_CACHE_MAX_BYTES,
    DEFAULT_AUDIO_CACHE_MAX_IDLE_SECONDS,
    DEFAULT_AUDIO_CACHE_TARGET_BYTES,
    DEFAULT_GENRE_ACTIVE_SECONDS,
    DEFAULT_GENRE_MAINTENANCE_LIMIT,
    DEFAULT_GENRE_REFRESH_AHEAD_SECONDS,
    home_music_service,
)


DEFAULT_AUDIO_WARM_LIMIT = 40
DEFAULT_FEED_AUDIO_WARM_LIMIT = 20


def _integer_environment(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def select_cache_tracks(warm_limit=DEFAULT_AUDIO_WARM_LIMIT):
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    listens = (
        MusicListen.query
        .order_by(MusicListen.last_played_at.desc())
        .limit(1000)
        .all()
    )
    retained = []
    warm = []
    seen = set()
    for listen in listens:
        last_played = listen.last_played_at
        if last_played.tzinfo is None:
            last_played = last_played.replace(tzinfo=timezone.utc)
        eligible = (
            listen.liked
            or last_played >= cutoff
            or listen.play_count >= 2
            or listen.completed_count >= 2
        )
        if not eligible or listen.track_id in seen:
            continue
        seen.add(listen.track_id)
        retained.append(listen.track_id)
        if (
            len(warm) < warm_limit
            and home_music_service.cached_audio_path(
                listen.track_id,
                touch=False,
            ) is None
        ):
            warm.append(listen.track_id)
    playlist_track_ids = (
        MusicPlaylistTrack.query
        .order_by(MusicPlaylistTrack.added_at.desc())
        .with_entities(MusicPlaylistTrack.track_id)
        .limit(2000)
        .all()
    )
    genre_track_ids = [
        (video_id,)
        for video_id in home_music_service.cached_genre_track_ids(
            max_entries=24,
            per_entry_limit=12,
        )
    ]
    for (track_id,) in playlist_track_ids + genre_track_ids:
        if track_id in seen:
            continue
        seen.add(track_id)
        retained.append(track_id)
        if (
            len(warm) < warm_limit
            and home_music_service.cached_audio_path(track_id, touch=False) is None
        ):
            warm.append(track_id)
    return retained, warm


def select_personalized_feeds(user_limit=20):
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    listens = (
        MusicListen.query
        .order_by(MusicListen.last_played_at.desc())
        .limit(2000)
        .all()
    )
    by_user = {}
    last_active = {}
    for listen in listens:
        last_played = listen.last_played_at
        if last_played.tzinfo is None:
            last_played = last_played.replace(tzinfo=timezone.utc)
        if not listen.liked and last_played < cutoff:
            continue
        by_user.setdefault(listen.user_id, []).append(listen)
        last_active[listen.user_id] = max(
            last_active.get(listen.user_id, last_played),
            last_played,
        )

    feeds = []
    for user_id in sorted(
        by_user,
        key=lambda value: last_active[value],
        reverse=True,
    )[:user_limit]:
        ranked = sorted(
            by_user[user_id],
            key=lambda listen: (
                bool(listen.liked),
                listen.completed_count or 0,
                listen.play_count or 0,
                listen.last_played_at,
            ),
            reverse=True,
        )[:20]
        feeds.append({
            "cache_key": f"user:{user_id}",
            "seed_ids": [listen.track_id for listen in ranked[:3]],
            "preferred_artists": [
                listen.artist.split(",", 1)[0]
                for listen in ranked
            ],
            "exclude_ids": [listen.track_id for listen in ranked],
        })
    return feeds


def _append_feed_warm_tracks(payload, tracks, seen, limit):
    if limit <= 0 or len(tracks) >= limit:
        return
    for track in payload.get("suggested_songs") or []:
        try:
            video_id = home_music_service.validate_video_id(track.get("id"))
        except (AttributeError, ValueError):
            continue
        if video_id in seen:
            continue
        seen.add(video_id)
        tracks.append(video_id)
        if len(tracks) >= limit:
            break


def run_maintenance():
    warm_limit = _integer_environment(
        "HOME_OS_MUSIC_AUDIO_WARM_LIMIT",
        DEFAULT_AUDIO_WARM_LIMIT,
    )
    retained, warm = select_cache_tracks(warm_limit=warm_limit)
    genre_result = home_music_service.maintain_genre_cache(
        refresh_ahead_seconds=_integer_environment(
            "HOME_OS_MUSIC_GENRE_REFRESH_AHEAD_SECONDS",
            DEFAULT_GENRE_REFRESH_AHEAD_SECONDS,
        ),
        active_seconds=_integer_environment(
            "HOME_OS_MUSIC_GENRE_ACTIVE_SECONDS",
            DEFAULT_GENRE_ACTIVE_SECONDS,
        ),
        max_entries=_integer_environment(
            "HOME_OS_MUSIC_GENRE_MAINTENANCE_LIMIT",
            DEFAULT_GENRE_MAINTENANCE_LIMIT,
        ),
    )
    feed_candidates = select_personalized_feeds(
        user_limit=_integer_environment(
            "HOME_OS_MUSIC_FEED_MAINTENANCE_LIMIT",
            20,
        )
    )
    feeds_refreshed = 0
    feeds_fresh = 0
    feeds_failed = 0
    feed_warm_limit = min(
        _integer_environment(
            "HOME_OS_MUSIC_FEED_AUDIO_WARM_LIMIT",
            DEFAULT_FEED_AUDIO_WARM_LIMIT,
        ),
        max(0, warm_limit - len(warm)),
    )
    feed_warm = []
    seen_feed_tracks = set(retained)
    for feed in feed_candidates:
        try:
            refreshed = home_music_service.refresh_personalized_home_if_due(
                feed["seed_ids"],
                feed["preferred_artists"],
                exclude_ids=feed["exclude_ids"],
                cache_key=feed["cache_key"],
            )
            payload = home_music_service.personalized_home(
                feed["seed_ids"],
                feed["preferred_artists"],
                exclude_ids=feed["exclude_ids"],
                cache_key=feed["cache_key"],
            )
        except Exception:
            feeds_failed += 1
        else:
            _append_feed_warm_tracks(
                payload,
                feed_warm,
                seen_feed_tracks,
                feed_warm_limit,
            )
            if refreshed:
                feeds_refreshed += 1
            else:
                feeds_fresh += 1
    result = home_music_service.maintain_audio_cache(
        list(dict.fromkeys(retained + feed_warm)),
        warm_track_ids=list(dict.fromkeys(warm + feed_warm)),
        max_bytes=_integer_environment(
            "HOME_OS_MUSIC_AUDIO_CACHE_MAX_BYTES",
            DEFAULT_AUDIO_CACHE_MAX_BYTES,
        ),
        target_bytes=_integer_environment(
            "HOME_OS_MUSIC_AUDIO_CACHE_TARGET_BYTES",
            DEFAULT_AUDIO_CACHE_TARGET_BYTES,
        ),
        max_idle_seconds=_integer_environment(
            "HOME_OS_MUSIC_AUDIO_CACHE_MAX_IDLE_SECONDS",
            DEFAULT_AUDIO_CACHE_MAX_IDLE_SECONDS,
        ),
    )
    return {
        **result,
        "retained_candidates": len(retained),
        "warm_candidates": len(warm),
        "feed_warm_candidates": len(feed_warm),
        "genres": genre_result,
        "feeds": {
            "eligible": len(feed_candidates),
            "refreshed": feeds_refreshed,
            "fresh": feeds_fresh,
            "failed": feeds_failed,
        },
    }


def main():
    application = create_app()
    with application.app_context():
        print(json.dumps(run_maintenance(), sort_keys=True))


if __name__ == "__main__":
    main()
