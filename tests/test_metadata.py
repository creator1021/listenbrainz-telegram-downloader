import tempfile
import unittest
import wave
from pathlib import Path

from mutagen.wave import WAVE

from app.matching import Track
from app.metadata import (
    MetadataError,
    embed_cover_art_if_missing,
    read_embedded_metadata,
    read_track_album,
    write_track_metadata,
)


class MetadataTests(unittest.TestCase):
    def test_writes_navidrome_fields_and_preserves_release_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 80)

            track = Track("Artist", "Title", 1, album="Real Album")
            album = write_track_metadata(path, track, "Fallback Album")
            tagged = WAVE(path)

            self.assertEqual(album, "Real Album")
            self.assertEqual(str(tagged.tags["TIT2"]), "Title")
            self.assertEqual(str(tagged.tags["TPE1"]), "Artist")
            self.assertEqual(str(tagged.tags["TPE2"]), "Artist")
            self.assertEqual(str(tagged.tags["TALB"]), "Real Album")
            self.assertEqual(
                read_embedded_metadata(path),
                read_embedded_metadata(path).__class__("Title", "Artist", "Real Album"),
            )

    def test_uses_fallback_album(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 80)

            album = write_track_metadata(
                path, Track("Artist", "Title", 1), "ListenBrainz Downloads"
            )

            self.assertEqual(album, "ListenBrainz Downloads")
            self.assertEqual(read_track_album(path), "ListenBrainz Downloads")

    def test_preserves_existing_album_before_using_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 80)

            write_track_metadata(
                path, Track("Artist", "Title", 1, album="Telegram Album")
            )
            album = write_track_metadata(
                path, Track("Artist", "Title", 2), "Configured Fallback"
            )

            self.assertEqual(album, "Telegram Album")
            self.assertEqual(read_track_album(path), "Telegram Album")

    def test_listenbrainz_release_overrides_existing_album(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 80)

            write_track_metadata(
                path, Track("Artist", "Title", 1, album="Telegram Album")
            )
            album = write_track_metadata(
                path, Track("Artist", "Title", 2, album="ListenBrainz Release")
            )

            self.assertEqual(album, "ListenBrainz Release")
            self.assertEqual(read_track_album(path), "ListenBrainz Release")

    def test_raw_aac_has_clear_limitation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.aac"
            path.write_bytes(b"not-a-real-aac")
            with self.assertRaisesRegex(MetadataError, "raw AAC"):
                write_track_metadata(path, Track("Artist", "Title", 1))

    def test_embeds_cover_without_replacing_existing_art(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 80)
            first_cover = Path(directory) / "first.jpg"
            second_cover = Path(directory) / "second.jpg"
            first_cover.write_bytes(b"\xff\xd8\xff-first-cover")
            second_cover.write_bytes(b"\xff\xd8\xff-second-cover")

            self.assertTrue(embed_cover_art_if_missing(path, first_cover))
            self.assertFalse(embed_cover_art_if_missing(path, second_cover))
            tagged = WAVE(path)

            self.assertEqual(
                tagged.tags.getall("APIC")[0].data, first_cover.read_bytes()
            )


if __name__ == "__main__":
    unittest.main()
