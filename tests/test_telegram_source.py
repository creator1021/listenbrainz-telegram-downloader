import tempfile
import unittest
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.database import Database
from app.matching import Track
from app.metadata import read_embedded_metadata
from app.telegram_source import TelegramSource


def audio_message(message_id, filename, title="", performer=""):
    file = SimpleNamespace(
        name=filename,
        mime_type="audio/mpeg",
        ext=Path(filename).suffix,
        size=5_000_000,
        title=title,
        performer=performer,
    )
    return SimpleNamespace(id=message_id, file=file, audio=object(), raw_text=filename)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.searched = []

    async def iter_messages(self, entity, search, limit):
        self.searched.append((entity, search, limit))
        for message in self.messages.get(entity, []):
            yield message


class FakeConnectClient:
    def __init__(self, dialogs=(), resolved=None):
        self.dialogs = dialogs
        self.resolved = resolved or {}
        self.get_input_entity_calls = []

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def get_input_entity(self, reference):
        self.get_input_entity_calls.append(reference)
        return self.resolved[reference]


class FakeSavedMessagesClient:
    def __init__(self, messages, source_file):
        self.messages = messages
        self.source_file = source_file

    async def get_messages(self, entity, limit):
        return list(reversed(self.messages[-limit:]))

    async def iter_messages(self, entity, min_id, reverse):
        for message in self.messages:
            if message.id > min_id:
                yield message

    async def download_media(self, message, file, thumb=None):
        destination = Path(file)
        destination.write_bytes(self.source_file.read_bytes())
        return str(destination)


class TelegramConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_channel_uses_live_dialog_input_entity(self):
        channel_id = -1001234567890
        client = FakeConnectClient(
            dialogs=(SimpleNamespace(id=channel_id, input_entity="channel-peer"),)
        )
        source = TelegramSource(
            client,
            (str(channel_id),),
            (".mp3",),
            150,
            50,
            76,
            Path("/downloads"),
        )

        await source.connect()

        self.assertEqual(source.channels, [(str(channel_id), "channel-peer")])
        self.assertEqual(client.get_input_entity_calls, [])

    async def test_public_username_uses_normal_entity_resolution(self):
        client = FakeConnectClient(resolved={"@music": "public-peer"})
        source = TelegramSource(
            client,
            ("@music",),
            (".mp3",),
            150,
            50,
            76,
            Path("/downloads"),
        )

        await source.connect()

        self.assertEqual(source.channels, [("@music", "public-peer")])
        self.assertEqual(client.get_input_entity_calls, ["@music"])

    async def test_missing_numeric_channel_has_clear_error_when_none_resolve(self):
        client = FakeConnectClient()
        source = TelegramSource(
            client,
            ("-1001234567890",),
            (".mp3",),
            150,
            50,
            76,
            Path("/downloads"),
        )

        with (
            self.assertLogs("app.telegram_source", level="WARNING") as logs,
            self.assertRaisesRegex(RuntimeError, "None of the configured"),
        ):
            await source.connect()

        self.assertIn("joined dialogs", " ".join(logs.output))

    async def test_unresolvable_channel_is_skipped_when_another_is_valid(self):
        good_id = -1009876543210
        client = FakeConnectClient(
            dialogs=(SimpleNamespace(id=good_id, input_entity="good-peer"),)
        )
        source = TelegramSource(
            client,
            ("-1001234567890", str(good_id)),
            (".mp3",),
            150,
            50,
            76,
            Path("/downloads"),
        )

        with self.assertLogs("app.telegram_source", level="WARNING"):
            await source.connect()

        self.assertEqual(source.channels, [(str(good_id), "good-peer")])


class TelegramPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_matching_channel_wins(self):
        track = Track("Daft Punk", "Get Lucky", 1)
        client = FakeClient(
            {
                "a": [audio_message(1, "unrelated.mp3", "One More Time", "Daft Punk")],
                "b": [audio_message(2, "Daft Punk - Get Lucky.mp3")],
                "c": [audio_message(3, "Daft Punk - Get Lucky lossless.flac")],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            source = TelegramSource(
                client,
                ("a", "b", "c"),
                (".mp3", ".flac"),
                150,
                50,
                76,
                Path(directory),
            )
            source.channels = [("A", "a"), ("B", "b"), ("C", "c")]
            candidate = await source.find(track)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.channel, "B")
        self.assertFalse(any(entity == "c" for entity, _, _ in client.searched))


class SavedMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_imports_forwarded_audio_once_with_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = root / "source.wav"
            with wave.open(str(source_file), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 80)
            message = SimpleNamespace(
                id=101,
                file=SimpleNamespace(
                    name="Manual Artist - Manual Song.wav",
                    mime_type="audio/wav",
                    ext=".wav",
                    size=source_file.stat().st_size,
                    title="Manual Song",
                    performer="Manual Artist",
                ),
                audio=object(),
                document=SimpleNamespace(thumbs=[]),
                raw_text="",
                date=datetime(2026, 9, 15, tzinfo=UTC),
            )
            client = FakeSavedMessagesClient([message], source_file)
            downloads = root / "downloads"
            database = Database(root / "state.sqlite3")
            telegram = TelegramSource(
                client,
                ("channel",),
                (".wav",),
                150,
                50,
                76,
                downloads,
            )

            first = await telegram.import_saved_messages(database, 20)
            with self.assertLogs("app.telegram_source", level="INFO") as logs:
                second = await telegram.import_saved_messages(database, 20)
            destination = downloads / "Manual Artist - Manual Song.wav"

            self.assertEqual(first, 1)
            self.assertEqual(second, 0)
            self.assertTrue(destination.is_file())
            metadata = read_embedded_metadata(destination)
            self.assertEqual(metadata.artist, "Manual Artist")
            self.assertEqual(metadata.title, "Manual Song")
            self.assertEqual(database.get_meta_int("last_seen_saved_message_id"), 101)
            self.assertIn("cursor=101 inspected=1 new=0", " ".join(logs.output))
            database.close()


if __name__ == "__main__":
    unittest.main()
