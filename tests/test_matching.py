import unittest

from app.matching import Track, acceptable, normalize, safe_component, score_candidate


class MatchingTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("Beyoncé feat. JAY-Z"), "beyonce jay z")

    def test_exact_noisy_filename_is_strong(self):
        track = Track("A. R. Rahman", "Jai Ho", 1)
        score = score_candidate(track, "A.R. Rahman - Jai Ho [320kbps] Tamil MP3.mp3")
        self.assertTrue(acceptable(score, 76), score)

    def test_right_title_wrong_artist_is_rejected(self):
        track = Track("Massive Attack", "Teardrop", 1)
        score = score_candidate(track, "Teardrop - Taylor Swift.mp3")
        self.assertFalse(acceptable(score, 76), score)

    def test_short_title_requires_whole_token(self):
        track = Track("U2", "One", 1)
        score = score_candidate(track, "Someone Else - U2.mp3")
        self.assertFalse(acceptable(score, 76), score)

    def test_embedded_tags_help(self):
        track = Track("Daft Punk", "Get Lucky", 1)
        score = score_candidate(
            track,
            "track_004.flac",
            embedded_title="Get Lucky",
            embedded_artist="Daft Punk",
        )
        self.assertTrue(acceptable(score, 76), score)

    def test_safe_filename(self):
        self.assertEqual(safe_component('AC/DC: "Thunder"'), "AC DC Thunder")


if __name__ == "__main__":
    unittest.main()
