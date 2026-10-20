"""
MeritMap CLI — run the pipeline headlessly for any profile.
Usage: python run_cli.py --name "Rahul" --github rahul-cs-dev --countries "Canada,Germany"
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from backend.schemas.models import PipelineState, StudentProfile, TestScore
from backend.events import get_history


async def run_cli(args):
    """Run the MeritMap pipeline from CLI."""
    from backend.graph.pipeline import run_pipeline

    run_id = f"cli-{args.name.lower().replace(' ', '-')[:10]}" if args.name else "cli-run"

    # Build profile from args
    countries = [c.strip() for c in args.countries.split(",")] if args.countries else []
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else []
    scores = []
    if args.ielts:
        scores.append(TestScore(test_name="IELTS", score=args.ielts))
    if args.gre:
        scores.append(TestScore(test_name="GRE", score=args.gre))

    profile = StudentProfile(
        name=args.name,
        nationality=args.nationality,
        degree=args.degree,
        field_of_study=args.field,
        cgpa=args.cgpa,
        cgpa_scale=args.cgpa_scale,
        github_username=args.github,
        target_countries=countries,
        target_degree_level=args.target_degree,
        target_fields=fields,
        needs_full_funding=args.full_funding,
        budget_max_yearly_usd=args.budget,
        test_scores=scores,
    )

    state = PipelineState(
        run_id=run_id,
        profile=profile,
        chaos_mode=args.chaos,
        status="running",
    )

    state_data = {
        "resume_path": args.resume,
        "marksheet_path": args.marksheet,
    }

    print(f"\n🏮 MeritMap CLI — Running pipeline for {args.name or 'Unknown'}")
    print(f"   Run ID: {run_id}")
    print("=" * 60)

    await run_pipeline(run_id, state, state_data)

    # Print results
    print("\n" + "=" * 60)
    print("📊 RESULTS")
    print("=" * 60)

    if state.top_matches:
        for i, m in enumerate(state.top_matches):
            opp = m.opportunity
            name = opp.name.value if hasattr(opp.name, 'value') else opp.name
            print(f"\n  {i+1}. {name} (Score: {m.fit_score})")
            print(f"     {m.why_it_fits}")
    else:
        print("  No matches found.")

    # Save to file
    out_path = Path("runs") / f"{run_id}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n💾 Results saved to {out_path}")

    # Print events summary
    events = get_history(run_id)
    warnings = [e for e in events if e.type.value == "warning"]
    errors = [e for e in events if e.type.value == "error"]
    print(f"\n📜 {len(events)} events total, {len(warnings)} warnings, {len(errors)} errors")


def main():
    parser = argparse.ArgumentParser(description="MeritMap CLI — AI Scholarship Finder")
    parser.add_argument("--name", type=str, help="Student name")
    parser.add_argument("--nationality", type=str, default="Indian")
    parser.add_argument("--degree", type=str, default="B.Tech")
    parser.add_argument("--field", type=str, default="Computer Science")
    parser.add_argument("--cgpa", type=float, default=8.2)
    parser.add_argument("--cgpa-scale", type=float, default=10.0)
    parser.add_argument("--github", type=str, help="GitHub username")
    parser.add_argument("--countries", type=str, default="Canada,Germany,Singapore")
    parser.add_argument("--target-degree", type=str, default="Masters")
    parser.add_argument("--fields", type=str, default="Computer Science,AI")
    parser.add_argument("--budget", type=float, default=5000)
    parser.add_argument("--full-funding", action="store_true")
    parser.add_argument("--resume", type=str, help="Path to resume PDF")
    parser.add_argument("--marksheet", type=str, help="Path to marksheet PDF")
    parser.add_argument("--ielts", type=float, help="IELTS score")
    parser.add_argument("--gre", type=float, help="GRE score")
    parser.add_argument("--chaos", action="store_true", help="Enable chaos mode")
    args = parser.parse_args()
    asyncio.run(run_cli(args))


if __name__ == "__main__":
    main()
