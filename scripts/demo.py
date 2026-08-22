"""Run the local two-phase pipeline against a trusted attendee sidecar."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from weave_common import Attendee, PipelineRequest, TranscriptTurn
from weave_ingestion.delivery import build_card

from agent.agents.enrichment import make_run_enrichment
from agent.agents.orchestrator import run_pipeline
from agent.context_sources.sources.prior_meeting_source import PriorMeetingSource
from tests.unit.fakes import FakeFirestoreClient, FakeSnapshot


def load_request(transcript_path: Path) -> PipelineRequest:
    attendee_path = transcript_path.with_suffix(".attendees.json")
    attendees = [
        Attendee.model_validate(raw)
        for raw in json.loads(attendee_path.read_text(encoding="utf-8"))
    ]
    by_name = {attendee.display_name.split()[0].casefold(): attendee for attendee in attendees}
    turns: list[TranscriptTurn] = []
    for index, line in enumerate(transcript_path.read_text(encoding="utf-8").splitlines()):
        speaker, text = line.split(":", 1)
        attendee = by_name.get(speaker.strip().casefold())
        turns.append(
            TranscriptTurn(
                turn_index=index,
                participant_id=attendee.participant_id if attendee else None,
                speaker_name=speaker.strip(),
                text=text.strip(),
            )
        )
    return PipelineRequest(
        transcript_turns=turns,
        conference_record_id="conferenceRecords/local-demo",
        meeting_date=date.today(),
        attendees=attendees,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=Path, required=True)
    args = parser.parse_args()

    request = load_request(args.transcript)
    source = PriorMeetingSource(
        client=FakeFirestoreClient(
            [
                FakeSnapshot(
                    "prior-1",
                    {
                        "description": "Sarah previously owned the launch metrics",
                        "visible_to": ["sarah@example.com"],
                        "created_at": datetime.now(UTC),
                    },
                )
            ]
        )
    )
    result = run_pipeline(
        request,
        enrich=make_run_enrichment([source]),
    )
    for bundle in result.bundles:
        print(f"\n=== {bundle.owner_email} ===")
        print(bundle.model_dump_json(indent=2))
        print("\nCard v2:")
        print(json.dumps(build_card(bundle), indent=2))


if __name__ == "__main__":
    main()
