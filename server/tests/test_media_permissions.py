import io
import os
import stat

from werkzeug.datastructures import FileStorage

from home_os.services.file_service import FileService


def test_shared_media_directory_modes_are_inherited(tmp_path):
    storage = tmp_path / "storage"
    media = storage / "Series"
    media.mkdir(parents=True)
    media.chmod(0o2770)
    service = FileService(storage, tmp_path / "trash")

    service.create_directory("Series/Show")
    upload = FileStorage(stream=io.BytesIO(b"media"), filename="episode.mkv")
    service.save_upload("Series/Show", upload)

    show = media / "Show"
    episode = show / "episode.mkv"
    assert stat.S_IMODE(show.stat().st_mode) == 0o2770
    assert stat.S_IMODE(episode.stat().st_mode) == 0o660
    assert show.stat().st_gid == media.stat().st_gid
    assert episode.stat().st_gid == media.stat().st_gid


def test_private_directory_modes_are_not_broadened(tmp_path):
    storage = tmp_path / "storage"
    private = storage / "Private"
    private.mkdir(parents=True)
    service = FileService(storage, tmp_path / "trash")

    old_umask = os.umask(0o077)
    try:
        upload = FileStorage(stream=io.BytesIO(b"private"), filename="notes.txt")
        service.save_upload("Private", upload)
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE((private / "notes.txt").stat().st_mode) == 0o600


def test_recursive_listing_returns_complete_tree_without_symlinks(tmp_path):
    storage = tmp_path / "storage"
    nested = storage / "HomeOS" / "Series" / "Show" / "Season 1"
    nested.mkdir(parents=True)
    (nested / "episode.mkv").write_bytes(b"episode")
    (storage / "outside").mkdir()
    (storage / "HomeOS" / "external").symlink_to(storage / "outside")
    service = FileService(storage, tmp_path / "trash")

    entries = service.list_directory_recursive("/HomeOS")

    assert [entry["path"] for entry in entries] == [
        "/HomeOS/Series",
        "/HomeOS/Series/Show",
        "/HomeOS/Series/Show/Season 1",
        "/HomeOS/Series/Show/Season 1/episode.mkv",
    ]


def test_recursive_listing_fails_instead_of_returning_partial_tree(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "one.txt").write_text("one")
    (storage / "two.txt").write_text("two")
    service = FileService(storage, tmp_path / "trash")
    service.MAX_RECURSIVE_ENTRIES = 1

    try:
        service.list_directory_recursive("/")
    except OverflowError:
        pass
    else:
        raise AssertionError("Expected oversized recursive listing to fail")
