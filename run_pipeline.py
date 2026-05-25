"""Run the project data pipeline steps in order."""

import argparse
import subprocess
import sys


STEPS = {
    1: "step1_prepare_source_projects.py",
    2: "step2_archive_project_files.py",
    3: "step3_extract_project_text.py",
    4: "step4_ai_project_analysis.py",
    5: "step5_fetch_coordinates.py",
    6: "step6_export_google_earth.py",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Run pipeline steps.")
    parser.add_argument("--from-step", type=int, default=1, choices=STEPS)
    parser.add_argument("--to-step", type=int, default=6, choices=STEPS)
    return parser.parse_args()


def main():
    args = parse_args()

    if args.from_step > args.to_step:
        print("Error: --from-step cannot be greater than --to-step.")
        return 1

    for step_number in range(args.from_step, args.to_step + 1):
        step_script = STEPS[step_number]
        print(f"\n========== Running step {step_number}: {step_script} ==========\n")

        result = subprocess.run([sys.executable, step_script], check=False)

        if result.returncode != 0:
            print(f"\nERROR: step {step_number} failed: {step_script}")
            return result.returncode

    print("\nPipeline finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
