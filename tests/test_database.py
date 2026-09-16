import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.matching import Track


class DatabaseTests(unittest.TestCase):
    def test_duplicate_listen_is_idempotent_and_track_downloads_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "state.sqlite3")
            track = Track("Artist", "Song", 100, "recording-id", "Album")
            self.assertTrue(database.register_listen(track))
            self.assertFalse(database.register_listen(track))
            self.assertEqual(len(database.due_tracks(10)), 1)
            database.mark_downloaded(track.key, "/downloads/song.mp3", "channel-a", 42)
            self.assertEqual(database.counts()["downloaded"], 1)
            self.assertEqual(database.due_tracks(10), [])
            downloaded = database.downloaded_tracks()
            self.assertEqual(downloaded[0].track.album, "Album")
            self.assertEqual(downloaded[0].path, Path("/downloads/song.mp3"))
            self.assertEqual(downloaded[0].source_channel, "channel-a")
            self.assertEqual(downloaded[0].source_message_id, 42)
            database.update_album(track.key, "Recovered Album")
            self.assertEqual(
                database.downloaded_tracks()[0].track.album, "Recovered Album"
            )
            database.close()


if __name__ == "__main__":
    unittest.main()
