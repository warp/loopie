from __future__ import annotations

import importlib
import unittest

try:
    from mcp_servers import meet_google
except ModuleNotFoundError as exc:
    meet_google = None  # type: ignore[assignment]
    GOOGLE_IMPORT_ERROR = exc
else:
    GOOGLE_IMPORT_ERROR = None


@unittest.skipIf(meet_google is None, f"Google client libraries unavailable: {GOOGLE_IMPORT_ERROR}")
class MeetGoogleHelpersTest(unittest.TestCase):
    def test_meeting_code_from_calendar_conference_id(self) -> None:
        ev = {"conferenceData": {"conferenceId": "ABC-DEFG-HIJ"}}

        self.assertEqual(meet_google.meeting_code_from_event(ev), "abc-defg-hij")

    def test_meeting_code_from_hangout_link(self) -> None:
        ev = {"hangoutLink": "https://meet.google.com/abc-defg-hij"}

        self.assertEqual(meet_google.meeting_code_from_event(ev), "abc-defg-hij")

    def test_meeting_code_from_entry_point(self) -> None:
        ev = {
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://meet.google.com/xyz-mnop-qrs"}
                ]
            }
        }

        self.assertEqual(meet_google.meeting_code_from_event(ev), "xyz-mnop-qrs")

    def test_normalize_entry_uses_last_participant_segment(self) -> None:
        entry = {
            "participant": "conferenceRecords/abc/participants/123",
            "startTime": "2026-04-26T10:00:00Z",
            "endTime": "2026-04-26T10:00:05Z",
            "text": "I will send the deck tomorrow.",
            "languageCode": "en-US",
        }

        self.assertEqual(
            meet_google._normalize_entry(entry),
            {
                "start_time": "2026-04-26T10:00:00Z",
                "end_time": "2026-04-26T10:00:05Z",
                "speaker": "123",
                "text": "I will send the deck tomorrow.",
                "language_code": "en-US",
            },
        )


@unittest.skipIf(meet_google is None, f"Google client libraries unavailable: {GOOGLE_IMPORT_ERROR}")
class OAuthScopesTest(unittest.TestCase):
    def test_oauth_setup_includes_meet_scopes(self) -> None:
        oauth_setup = importlib.import_module("scripts.oauth_setup")

        for scope in meet_google.MEET_SCOPES:
            self.assertIn(scope, oauth_setup.OAUTH_SCOPES)


if __name__ == "__main__":
    unittest.main()
