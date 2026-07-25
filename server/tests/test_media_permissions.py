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
