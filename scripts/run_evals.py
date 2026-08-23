"""Run ADK eval cases serially to stay within low Gemini API quotas."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class EvalSuite:
    path: str
    case_ids: tuple[str, ...]
    agent_selector: str | None = None


SUITES = (
    EvalSuite(
        path="eval/extraction_cases.json",
        case_ids=(
            "clear_accept_with_deadline",
            "explicit_decline",
            "assignment_with_silence",
            "explicit_deferral",
            "accepted_reassignment",
            "accept_without_deadline",
            "ambiguous_owner",
            "first_person_reference_resolved",
            "unidentifiable_third_person",
            "second_person_is_the_owner",
        ),
    ),
    EvalSuite(
        path="eval/leakage_cases.json",
        case_ids=(
            "owner_a_sees_only_alpha",
            "owner_b_never_sees_alpha",
            "missing_principal_searches_nothing",
        ),
        agent_selector="enrichment",
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay-seconds", type=float, default=25.0)
    args = parser.parse_args()
    if args.delay_seconds < 0:
        parser.error("--delay-seconds must be non-negative")

    cases = [(suite, case_id) for suite in SUITES for case_id in suite.case_ids]
    for index, (suite, case_id) in enumerate(cases):
        environment = os.environ.copy()
        if suite.agent_selector:
            environment["WEAVE_EVAL_AGENT"] = suite.agent_selector
        else:
            environment.pop("WEAVE_EVAL_AGENT", None)
        subprocess.run(
            [
                "adk",
                "eval",
                "agent",
                f"{suite.path}:{case_id}",
                "--config_file_path",
                "eval/eval_config.json",
            ],
            check=True,
            env=environment,
        )
        if index < len(cases) - 1 and args.delay_seconds:
            time.sleep(args.delay_seconds)


if __name__ == "__main__":
    main()
