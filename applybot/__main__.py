"""CLI: python -m applybot <command> ...

  init                     create profile.yaml from the example
  apply --job jobs/x.json  fill the form; auto-submits when clean
                           (--park to stop at review instead)
  agent --job jobs/x.yaml [--park-like default] [--submit] [--profile DIR]
                           run the generic perceive->reason->act->verify loop.
                           Parks at review by default; --submit is the
                           explicit opt-in that lets it click submit/apply
                           (submits for real, verified against confirmation
                           markers). --profile keeps a persistent browser
                           profile so a manual sign-in survives between runs.
  signin --job jobs/x.yaml --profile DIR
                           open the job page in a persistent profile so YOU
                           sign in yourself (the bot never touches passwords)
  status --job-id x        show current state + open questions
  answer --job-id x --answers '{"q1": "Yes"}'
                           record your explicit answers, then re-run with resume
  resume --job-id x --job jobs/x.json
  approve --job-id x --by "Sree"   manual override (rarely needed now)
  submit --job-id x --job jobs/x.json   manual override, needs approval
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from applybot.runner import Runner
from applybot.state import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m applybot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create profile.yaml from the example")

    p = sub.add_parser("apply", help="fill the form; auto-submits when clean")
    p.add_argument("--job", required=True, help="path to job spec yaml/json")
    p.add_argument("--headed", action="store_true", help="show the browser")
    p.add_argument("--park", action="store_true",
                   help="stop at the review screen instead of auto-submitting")

    p = sub.add_parser("snapshot",
                       help="read-only question inventory of the form (fills nothing)")
    p.add_argument("--job", required=True, help="path to job spec yaml/json")
    p.add_argument("--headed", action="store_true", help="show the browser")

    p = sub.add_parser("agent",
                       help="run the generic agent loop (parks at review unless --submit)")
    p.add_argument("--job", required=True, help="path to job spec yaml/json")
    p.add_argument("--headed", action="store_true", help="show the browser")
    p.add_argument("--max-steps", type=int, default=12,
                   help="cap on perceive/reason/act cycles")
    p.add_argument("--submit", action="store_true",
                   help="explicit opt-in: allow the agent to click "
                        "submit/apply at the end (submits for real)")
    p.add_argument("--profile", default=None,
                   help="persistent browser profile dir; keeps your "
                        "sign-in between runs (use with signin first)")

    p = sub.add_parser("signin",
                       help="open the job page in a persistent profile so "
                            "you can sign in yourself (the bot never fills "
                            "passwords)")
    p.add_argument("--job", required=True, help="path to job spec yaml/json")
    p.add_argument("--profile", required=True,
                   help="persistent browser profile dir to sign in under")

    p = sub.add_parser("status", help="show state and open questions")
    p.add_argument("--job-id", required=True)

    p = sub.add_parser("answer", help="record your answers to open questions")
    p.add_argument("--job-id", required=True)
    p.add_argument("--answers", required=True, help="JSON object {field_id: answer}")

    p = sub.add_parser("resume", help="re-run the fill after answering")
    p.add_argument("--job-id", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--park", action="store_true",
                   help="stop at the review screen instead of auto-submitting")

    p = sub.add_parser("approve", help="record explicit approval to submit")
    p.add_argument("--job-id", required=True)
    p.add_argument("--by", required=True, help="who approved")

    p = sub.add_parser("submit", help="submit (needs prior approval)")
    p.add_argument("--job-id", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--headed", action="store_true")

    args = ap.parse_args(argv)
    runner = Runner()

    if args.cmd == "init":
        dst = Path("profile.yaml")
        if dst.exists():
            print("profile.yaml already exists; not overwriting.")
            return 0
        src = Path(__file__).parent / "profile.example.yaml"
        shutil.copy(src, dst)
        print("created profile.yaml - fill in your contact details.")
        print("It is git-ignored: never commit it.")
        return 0

    if args.cmd == "apply":
        runner.apply(args.job, headless=not args.headed,
                     auto_submit=not args.park)
        return 0

    if args.cmd == "agent":
        from applybot.agent import run_agent
        run_agent(args.job, headless=not args.headed,
                  max_steps=args.max_steps, allow_submit=args.submit,
                  user_data_dir=args.profile)
        return 0

    if args.cmd == "signin":
        from applybot.config import JobSpec
        from applybot import browser as B
        job = JobSpec.load(args.job)
        with B.launch(headless=False,
                      user_data_dir=args.profile) as page:
            page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
            print(f"opened {job.url}")
            print("Sign in yourself in this window — the bot never "
                  "touches passwords or 2FA.")
            input("Press Enter here once you are signed in and the "
                  "application form is visible... ")
        print("profile saved under", args.profile)
        print("Now run: python -m applybot agent --job", args.job,
              "--headed --profile", args.profile, "[--submit]")
        return 0

    if args.cmd == "snapshot":
        runner.snapshot(args.job, headless=not args.headed)
        return 0

    if args.cmd == "status":
        data = Store().load(args.job_id)
        print(f"job:   {data.get('job_id')}")
        print(f"state: {data.get('state')}")
        job = data.get("job") or {}
        if job:
            print(f"role:  {job.get('role')} @ {job.get('company')}")
        needs = data.get("needs") or []
        if needs:
            print(f"\nopen questions ({len(needs)}):")
            for n in needs:
                opts = f"  options: {', '.join(n['options'])}" if n.get("options") else ""
                print(f"  - id: {n['field_id']}\n    [{n['kind']}] {n['label']}{opts}")
        if data.get("approval"):
            print(f"\napproval: {data['approval']}")
        return 0

    if args.cmd == "answer":
        answers = json.loads(args.answers)
        runner.answer(args.job_id, answers)
        return 0

    if args.cmd == "resume":
        runner.apply(args.job, headless=not args.headed,
                     auto_submit=not args.park)
        return 0

    if args.cmd == "approve":
        runner.approve(args.job_id, args.by)
        return 0

    if args.cmd == "submit":
        runner.submit(args.job_id, args.job, headless=not args.headed)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
