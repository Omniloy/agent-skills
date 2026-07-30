#!/usr/bin/env python3
"""epic-loop helper: backlog discovery + review-gate detection via `gh`.

Subcommands:
  epics          --repo O/R                 list Epics (label:epic) + milestone + sub-issues + states (JSON)
  next           --repo O/R [--skip LBL...]  the next Epic to work (first with an open, non-skipped sub-issue)
  audit          --repo O/R                 flag backlog-hygiene gaps: Epics done-but-open, milestones
                                            done-but-open, closed sub-issues whose Epic task-box is unticked
  review-status  --repo O/R --pr N [--gate greptile|ci|human]

stdlib only; needs `gh` authenticated. All output is JSON on stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def gh(args: list[str]) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        raise SystemExit(f"gh {' '.join(args)} failed")
    return p.stdout


def gh_json(args: list[str]):
    out = gh(args).strip()
    return json.loads(out) if out else []


def sub_issue_numbers(repo: str, epic_num: int, epic_body: str) -> list[int]:
    """Native sub-issues first; fall back to `- [ ] #n` task list in the body."""
    nums: list[int] = []
    try:
        data = gh_json(["api", f"repos/{repo}/issues/{epic_num}/sub_issues"])
        nums = [it["number"] for it in data]
    except SystemExit:
        nums = []
    if not nums:
        import re

        nums = [int(m) for m in re.findall(r"- \[[ xX]\] #(\d+)", epic_body or "")]
    return sorted(set(nums))


def _ticked_boxes(body: str) -> set[int]:
    """Issue numbers whose task-list box is CHECKED (`- [x] #n`) in the body."""
    import re

    return {int(m) for m in re.findall(r"- \[[xX]\] #(\d+)", body or "")}


def list_epics(repo: str) -> list[dict]:
    epics = gh_json(
        ["issue", "list", "--repo", repo, "--label", "epic", "--state", "all",
         "--limit", "100", "--json", "number,title,state,body,milestone"]
    )
    epics.sort(key=lambda e: e["number"])
    out = []
    for e in epics:
        body = e.get("body", "")
        subs = sub_issue_numbers(repo, e["number"], body)
        ticked = _ticked_boxes(body)
        sub_states = []
        for n in subs:
            s = gh_json(["issue", "view", str(n), "--repo", repo, "--json", "number,title,state,labels"])
            sub_states.append({
                "number": s["number"], "title": s["title"], "state": s["state"],
                "labels": [lbl["name"] for lbl in s.get("labels", [])],
                "box_ticked": s["number"] in ticked,
            })
        ms = e.get("milestone") or {}
        out.append({
            "number": e["number"], "title": e["title"], "state": e["state"],
            "milestone": ms.get("title"),
            "open_subs": sum(1 for s in sub_states if s["state"] == "OPEN"),
            "sub_issues": sub_states,
        })
    return out


def audit_backlog(repo: str) -> dict:
    """Flag the common 'forgot to update the backlog' gaps so they get fixed."""
    epics = list_epics(repo)
    flags: list[dict] = []
    for e in epics:
        subs = e["sub_issues"]
        closed = [s for s in subs if s["state"] == "CLOSED"]
        if subs and e["state"] == "OPEN" and len(closed) == len(subs):
            flags.append({"type": "close_epic", "epic": e["number"], "title": e["title"],
                          "fix": f"gh issue close {e['number']} --repo {repo}"})
        for s in closed:
            if not s["box_ticked"]:
                flags.append({"type": "tick_box", "epic": e["number"], "sub": s["number"],
                              "fix": f"edit Epic #{e['number']} body: '- [ ] #{s['number']}' -> '- [x] #{s['number']}'"})
        if e["state"] == "OPEN" and not e["milestone"]:
            flags.append({"type": "epic_no_milestone", "epic": e["number"], "title": e["title"]})
    milestones = gh_json(["api", f"repos/{repo}/milestones?state=all&per_page=100"])
    ms_out = []
    for m in milestones:
        done_but_open = (m.get("state") == "open" and m.get("open_issues", 0) == 0
                         and m.get("closed_issues", 0) > 0)
        ms_out.append({"number": m["number"], "title": m["title"], "state": m["state"],
                       "open_issues": m.get("open_issues"), "closed_issues": m.get("closed_issues")})
        if done_but_open:
            flags.append({"type": "close_milestone", "milestone": m["number"], "title": m["title"],
                          "fix": f"gh api -X PATCH repos/{repo}/milestones/{m['number']} -f state=closed"})
    return {"milestones": ms_out, "epics": epics, "flags": flags, "clean": not flags}


def next_epic(repo: str, skip: list[str]) -> dict | None:
    for e in list_epics(repo):
        actionable = [
            s for s in e["sub_issues"]
            if s["state"] == "OPEN" and not (set(skip) & set(s["labels"]))
            and not s["title"].lower().startswith(("(fase posterior)", "(plantilla"))
        ]
        if actionable:
            return {"epic": {"number": e["number"], "title": e["title"]},
                    "actionable_subs": actionable,
                    "skipped_subs": [s for s in e["sub_issues"]
                                     if s["state"] == "OPEN" and s not in actionable]}
    return None


def review_status(repo: str, pr: int, gate: str) -> dict:
    prj = gh_json(["pr", "view", str(pr), "--repo", repo,
                   "--json", "state,body,headRefOid,reviewDecision,statusCheckRollup"])
    import re
    head_full = prj.get("headRefOid") or ""
    head = head_full[:9]
    if gate == "greptile":
        body = prj.get("body") or ""
        # Parse the actual "Confidence Score: N/5" (not just ==5/5).
        m = re.search(r"Confidence Score:\s*(\d+)\s*/\s*5", body)
        score = int(m.group(1)) if m else None
        # Greptile references the reviewed commit (full sha) in its summary's commit link.
        summary_on_head = bool(head_full and head_full in body)
        # last @greptileai re-review request, and whether greptile 👍'd it
        icomments = gh_json(["api", f"repos/{repo}/issues/{pr}/comments"])
        mine = [c for c in icomments if "@greptileai" in (c.get("body") or "")]
        last_req_ts = mine[-1]["created_at"] if mine else ""
        liked = False
        if mine:
            reacts = gh_json(["api", f"repos/{repo}/issues/comments/{mine[-1]['id']}/reactions"])
            liked = any(r.get("content") == "+1"
                        and r.get("user", {}).get("login") == "greptile-apps[bot]" for r in reacts)
        # ACTIONABLE = greptile inline comments created AFTER the last re-review request.
        # (GitHub repositions old, already-resolved comments onto the head commit, so a
        # commit_id==head check is unreliable; timestamp vs the last request is correct.)
        comments = gh_json(["api", f"repos/{repo}/pulls/{pr}/comments"])
        new_comments = [c for c in comments
                        if c.get("user", {}).get("login") == "greptile-apps[bot]"
                        and (not last_req_ts or (c.get("created_at") or "") > last_req_ts)]
        # The review for THIS round is COMPLETE when greptile added nothing new inline AND
        # it either 👍'd our last re-review request OR its summary covers the head commit.
        # A 👍 with no new comment means greptile is DONE — even if the score is < 5/5.
        review_complete = (not new_comments) and (liked or (summary_on_head and score is not None))
        if new_comments:
            status = "COMMENTS"            # address the inline comments
        elif review_complete and score == 5:
            status = "DONE"                # merge
        elif review_complete and score is not None:
            status = "REVIEW_BELOW_5"      # complete but <5/5 → read reason, fix it or respond+merge
        else:
            status = "PENDING"             # greptile still reviewing
        # The score line + rationale, so the loop can act on a <5/5 verdict.
        excerpt = re.sub(r"<[^>]+>", "", body[m.start():m.start() + 700]).strip() if m else ""
        return {"gate": "greptile", "head": head, "score": (f"{score}/5" if score is not None else None),
                "score_5_5": score == 5, "summary_on_head": summary_on_head,
                "liked_last_request": liked, "new_actionable_comments": len(new_comments),
                "review_complete": review_complete, "status": status, "reason_excerpt": excerpt[:500]}
    if gate == "ci":
        rollup = prj.get("statusCheckRollup") or []
        bad = [c for c in rollup if (c.get("conclusion") or c.get("state") or "").upper()
               in {"FAILURE", "ERROR", "CANCELLED"}]
        pending = [c for c in rollup if (c.get("status") or "").upper() in {"IN_PROGRESS", "QUEUED", "PENDING"}]
        comments = gh_json(["api", f"repos/{repo}/pulls/{pr}/comments"])
        status = "COMMENTS" if comments else ("PENDING" if pending or not rollup
                                              else ("FAILED" if bad else "DONE"))
        return {"gate": "ci", "head": head, "failed": len(bad), "pending": len(pending),
                "open_comments": len(comments), "status": status}
    # human
    return {"gate": "human", "head": head, "reviewDecision": prj.get("reviewDecision"),
            "status": "DONE" if prj.get("reviewDecision") == "APPROVED" else "PENDING"}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("epics"); pe.add_argument("--repo", required=True)
    pa = sub.add_parser("audit"); pa.add_argument("--repo", required=True)
    pn = sub.add_parser("next"); pn.add_argument("--repo", required=True)
    pn.add_argument("--skip", nargs="*", default=["needs-human"])
    pr = sub.add_parser("review-status"); pr.add_argument("--repo", required=True)
    pr.add_argument("--pr", type=int, required=True)
    pr.add_argument("--gate", default="greptile", choices=["greptile", "ci", "human"])
    a = ap.parse_args()
    if a.cmd == "epics":
        print(json.dumps(list_epics(a.repo), indent=2))
    elif a.cmd == "audit":
        print(json.dumps(audit_backlog(a.repo), indent=2))
    elif a.cmd == "next":
        print(json.dumps(next_epic(a.repo, a.skip), indent=2))
    elif a.cmd == "review-status":
        print(json.dumps(review_status(a.repo, a.pr, a.gate), indent=2))


if __name__ == "__main__":
    main()
