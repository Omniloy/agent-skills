#!/usr/bin/env python3
"""Static review of a conversation flow document: the defects we already paid for, found
before a caller finds them.

Every rule below comes from a real incident (the tag in brackets points at
`references/defect-catalog.md`). Structural rules are exact; the rules that read prompt text
are heuristics over Spanish and English wording, so they report `warn`/`info`, never `error`,
and a finding is a question for the author, not a verdict.

    python3 flow_lint.py --env stg --flow <uuid>          # read the live document
    python3 flow_lint.py --file flow.json                  # or a local copy
    python3 flow_lint.py --env stg --flow <uuid> --json    # machine-readable
    python3 flow_lint.py ... --min warn                    # hide info

Exit status 1 when there is at least one `error`.

With `--env`, three checks that need the database also run: the knowledge-base collection is
linked, the flow is (or is not) what an api_key serves, and the version is healthy.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Set

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from flowdb import host, iter_webhooks, text  # noqa: E402

SEVERITIES = ("error", "warn", "info")
DETERMINISTIC = {"end", "audio", "transfer_call", "branch"}
#: Tools every conversation node has without listing them.
BUILTINS = {"update_flow_variables", "advance_conversation_flow", "hang_up", "get_node_content",
            "transfer_to_human", "lookup_company_info", "read_data_protection_message"}
#: Grammar the LLM function-calling API accepts (flow_node_agent._PUBLISHABLE_TOOL_NAME).
TOOL_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
VAR_REF = re.compile(r"\{\{\s*([\w./]+)\s*\}\}")

# Heuristic vocabulary, es + en.
HUMAN = re.compile(r"\b(una persona|un[ao]? (agente|operador[a]?|compañer[oa])|recepci[oó]n|"
                   r"a human|a person|an agent|an operator|reception)\b", re.I)
DECLINE = re.compile(r"(desiste|no quiere|rechaza|prefiere (dejarlo|no)|se arrepiente|dice que no|"
                     r"declin|does not want|doesn'?t want|refuses|changes (his|her|their) mind)", re.I)
FAILURE_NODE = re.compile(r"(error|fallo|fail|ko)\b", re.I)
#: Exits that are not the happy path: a failure, or the caller walking away.
NOT_HAPPY = re.compile(r"(error|fallo|fail|_ko\b|adios|desiste|no quiere|decide no|rechaza|declin)", re.I)
#: "On entry, call X before saying anything" — only phrasings that tie the call to ENTERING the
#: node. "PASO 1, when they confirm: call X" is a later turn and has tools.
TOOL_FIRST = re.compile(r"((antes de decir nada|nada más entrar|al entrar( en este nodo)?|en cuanto entres)"
                        r"[^.\n]{0,60}\b(llama|consulta|usa)\b|"
                        r"(before (saying|you say) anything|on entry|as soon as you enter)[^.\n]{0,60}\bcall\b)", re.I)
ANNOUNCE_THEN_EXIT = re.compile(r"(dile|dígale|informa|avisa|anuncia|tell (him|her|them)|announce)"
                                r"[^.\n]{0,120}\b(y|and) (toma|take|transita|pasa a|ve a|go to)", re.I)
RULE_WORDS = re.compile(r"\b(NUNCA|NO (digas|leas|llames)|nunca|jam[aá]s|llama a|usa la herramienta|"
                        r"never|do not (say|call|read)|call the tool)\b")
TRANSFER_ANNOUNCE = re.compile(r"(dile|avísale|anuncia|tell (him|her|them)|announce).{0,80}"
                               r"(pas|transfer)", re.I)
NEGATIVE_CLAIM = re.compile(r"(dile|dígale|contesta|responde|tell (him|her|them)).{0,60}"
                            r"\b(no (tiene|existe|hay|consta)|no se puede citar|you have no|does not exist)", re.I)


@dataclass
class Finding:
    rule: str
    severity: str
    where: str
    message: str
    tag: str = ""

    def key(self) -> tuple:
        return (self.rule, self.where, self.message)

    def __str__(self) -> str:
        tag = f"  [{self.tag}]" if self.tag else ""
        return f"{self.severity.upper():5} {self.rule} {self.where}: {self.message}{tag}"


def _nodes(doc: dict) -> Dict[str, dict]:
    return {n["node_id"]: n for n in doc.get("nodes", []) or []}


def _edges(n: dict) -> List[dict]:
    """The node's own edges plus the two special ones, which also move the flow."""
    out = list(n.get("edges") or [])
    for special in ("skip_response_edge", "transfer_failed_edge"):
        if n.get(special):
            out.append({**n[special], "_special": special})
    return out


def _model_edges(n: dict) -> List[dict]:
    """Edges the LLM can see and take: prompt edges, and equation edges without eval_after_tools."""
    return [e for e in n.get("edges") or []
            if e.get("edge_type") != "equation" or not e.get("eval_after_tools")]


def _languages(doc: dict) -> Set[str]:
    langs = {doc.get("default_language") or "es"}
    for n in doc.get("nodes", []) or []:
        for f in ("instruction", "pre_message", "goodbye_message"):
            if isinstance(n.get(f), dict):
                langs |= {k for k, v in n[f].items() if v}
    return langs


def _tool_index(doc: dict) -> Dict[str, dict]:
    idx = {}
    for t in doc.get("tools", []) or []:
        for k in (t.get("tool_id"), t.get("name")):
            if k:
                idx[k] = t
    return idx


def _written_vars(doc: dict) -> Set[str]:
    """Variables something can write: defaults, response variables (plain and namespaced),
    `<tool>.result`, and anything a prompt tells the model to put in update_flow_variables."""
    out = set((doc.get("default_dynamic_variables") or {}).keys())
    tools = list(doc.get("tools", []) or [])
    for n in doc.get("nodes", []) or []:
        tools += list(n.get("prefetch_tools") or [])
    for t in tools:
        name = t.get("name") or ""
        out.add(f"{name}.result")
        for v in (t.get("response_variables") or {}):
            out |= {v, f"{name}.{v}"}
    return out


def lint(doc: dict) -> List[Finding]:
    F: List[Finding] = []
    add = lambda *a, **k: F.append(Finding(*a, **k))  # noqa: E731
    nodes = _nodes(doc)
    tools = _tool_index(doc)
    tool_names = {t.get("name") for t in doc.get("tools", []) or []}
    start = doc.get("start_node_id")
    langs = _languages(doc)
    written = _written_vars(doc)
    end_nodes = {nid for nid, n in nodes.items() if n.get("node_type") == "end"}
    globals_ = {nid: n for nid, n in nodes.items() if n.get("is_global")}

    # ---------------------------------------------------------------- graph
    if start not in nodes:
        add("G01", "error", "flow", f"start_node_id {start!r} is not a node")
    for nid, n in nodes.items():
        for e in _edges(n):
            if e.get("target_node_id") not in nodes:
                add("G02", "error", f"{nid}/{e.get('edge_id')}",
                    f"target {e.get('target_node_id')!r} does not exist")
    reach, todo = set(), [start] + list(globals_)
    while todo:
        cur = todo.pop()
        if cur in reach or cur not in nodes:
            continue
        reach.add(cur)
        todo += [e.get("target_node_id") for e in _edges(nodes[cur])]
    for nid in nodes:
        if nid not in reach:
            add("G03", "warn", nid, "unreachable from the start node and from every global node")
    for nid, n in globals_.items():
        if not (n.get("global_condition") or "").strip():
            add("G04", "error", nid, "is_global with an EMPTY global_condition: the runtime drops it "
                "silently, so this node is never offered", tag="global-empty")
    if not end_nodes:
        add("G05", "warn", "flow", "no `end` node: every goodbye is improvised and the call ends by hang_up")

    # ---------------------------------------------------------------- edges
    for nid, n in nodes.items():
        conv_prompt = n.get("node_type") == "conversation" and n.get("instruction_type") == "prompt"
        for e in n.get("edges") or []:
            where = f"{nid}/{e.get('edge_id')}"
            if e.get("edge_type") == "equation":
                eq = e.get("equation") or ""
                if not eq.strip():
                    add("E01", "error", where, "equation edge with an empty equation")
                after = e.get("eval_after_tools") or []
                if not after and n.get("node_type") != "branch":   # a branch evaluates on entry
                    node_vars = {v for tid in n.get("tool_ids") or [] if tid in tools
                                 for v in (tools[tid].get("response_variables") or {})}
                    # Nothing of this node's own tools writes the variable: it is written by
                    # update_flow_variables, a flow-control tool whose follow-up turn is cancelled.
                    sev = "warn" if set(VAR_REF.findall(eq)) & node_vars else "error"
                    add("E02", sev, where, "bare equation edge (no eval_after_tools): nobody evaluates "
                        "it after the tool runs; the node stalls ~15-20 s until the inactivity rescue",
                        tag="bare-equation")
                for t in after:
                    if t not in tool_names:
                        add("E03", "error", where, f"eval_after_tools names {t!r}, which is not in "
                            "flow_document.tools[]: the edge never fires")
                for var in VAR_REF.findall(eq):
                    if var not in written:
                        add("E04", "warn", where, f"equation reads {{{{{var}}}}} but no default, "
                            "response_variable or tool result declares it")
                if re.search(r'!=\s*""|!=\s*\'\'', eq) and after:
                    add("E05", "info", where, "`!= \"\"` over a tool result: an equation cannot see a "
                        "tool ERROR (a failed webhook is a successful result whose text starts with "
                        "'Error:'); make sure a model-visible exit covers the failure", tag="error-invisible")
            desc = text(e.get("description"))
            tgt = nodes.get(e.get("target_node_id"), {})
            if DECLINE.search(desc) and (tgt.get("node_type") == "end" and FAILURE_NODE.search(
                    e.get("target_node_id", "") + " " + (tgt.get("node_name") or ""))):
                add("E06", "warn", where, f"a DECLINE ({DECLINE.search(desc).group(0)!r}) routed to the "
                    f"error ending {e.get('target_node_id')}: declining is a decision, not a failure",
                    tag="decline-is-not-failure")
        if conv_prompt:
            model = _model_edges(n)
            eq_hidden = [e for e in n.get("edges") or [] if e not in model]
            if eq_hidden and model and all(NOT_HAPPY.search(e.get("edge_id", "") + " " +
                                                            text(e.get("description"))) for e in model):
                add("E07", "warn", nid, "the happy exit is an eval_after_tools equation and every edge the "
                    "model can see is a failure or decline exit: when it advances on its own it can only choose "
                    "wrong. Harden those descriptions with 'ONLY if …; otherwise the transition is "
                    "automatic'", tag="race")
            into_end = [e for e in model if e.get("target_node_id") in end_nodes]
            if len(into_end) > 1:
                add("E08", "info", nid, f"{len(into_end)} model-takeable edges into end nodes: the ENDING "
                    "rule falls back to 'say goodbye and hang_up' instead of naming one exit")
            descs = [text(e.get("description")) for e in model]
            for i, a in enumerate(descs):
                for b in descs[i + 1:]:
                    k = 0
                    while k < min(len(a), len(b)) and a[k] == b[k]:
                        k += 1
                    if k >= 120:
                        add("E09", "info", nid, f"two edge descriptions share a {k}-char preamble: put the "
                            "discriminant FIRST", tag="discriminant-last")
            if len(model) > 7:
                add("E10", "info", nid, f"{len(model)} model-visible edges (plus globals): each one competes; "
                    "keep only the ones the prompt actually offers", tag="edge-dilution")

    # ---------------------------------------------------------------- nodes
    for nid, n in nodes.items():
        ntype, itype = n.get("node_type"), n.get("instruction_type")
        instr = text(n.get("instruction"))
        # fixed texts must exist in every language the flow speaks
        spoken = [f for f in ("pre_message", "goodbye_message") if n.get(f)]
        if itype == "static_text" and n.get("instruction"):
            spoken.append("instruction")
        for f in spoken:
            have = set(n[f].keys()) if isinstance(n[f], dict) else {doc.get("default_language") or "es"}
            missing = langs - have
            if missing:
                add("N01", "warn", f"{nid}.{f}", f"fixed text read aloud has no {sorted(missing)}: the "
                    "runtime silently falls back to the default language", tag="language")
        if itype == "static_text" and ntype == "conversation" and RULE_WORDS.search(instr):
            add("N02", "warn", nid, f"static_text instruction looks like a RULE "
                f"({RULE_WORDS.search(instr).group(0)!r}): it is read ALOUD to the caller")
        if ntype == "transfer_call":
            if instr.strip():
                add("N03", "info", nid, "transfer_call never speaks its instruction (dead text); the code "
                    "announces every transfer itself")
            if TRANSFER_ANNOUNCE.search(n.get("global_condition") or ""):
                add("N04", "warn", nid, "global_condition asks the model to announce the transfer: the "
                    "code already does, so the caller hears it twice")
            if not n.get("transfer_failed_edge"):
                add("N05", "warn", nid, "no transfer_failed_edge: on a failed transfer the runtime's "
                    "fail-safe reads the dialled number aloud (an internal extension cannot be dialled "
                    "from outside)")
            num = str(n.get("transfer_number") or "")
            if num and "{{" not in num:
                add("N06", "info", nid, "transfer_number is a literal: keep it in a variable so each "
                    "environment can differ without touching the node")
        if ntype == "conversation" and itype == "prompt":
            has_human_edge = any(
                nodes.get(e.get("target_node_id"), {}).get("node_type") == "transfer_call"
                or any(nodes.get(x.get("target_node_id"), {}).get("node_type") == "transfer_call"
                       for x in _edges(nodes.get(e.get("target_node_id"), {})))
                for e in _model_edges(n))
            if HUMAN.search(instr) and not has_human_edge:
                add("N07", "warn", nid, "the prompt offers a person but no edge leads to one: it relies on "
                    "a global jump whose condition may say the CALLER must ask", tag="missing-exit")
            if ANNOUNCE_THEN_EXIT.search(instr):
                add("N08", "warn", nid, f"'tell them X and take exit Y' "
                    f"({ANNOUNCE_THEN_EXIT.search(instr).group(0)[:60]!r}…): the model often speaks and then "
                    "waits for a reply that never comes (~17-20 s). Test that the exit is taken in the SAME "
                    "turn, or use static_text + skip_response_edge / let the destination speak",
                    tag="announce-and-wait")
            if NEGATIVE_CLAIM.search(instr):
                add("N09", "info", nid, f"asserts a negative ({NEGATIVE_CLAIM.search(instr).group(0)!r}): "
                    "an empty lookup means 'I cannot see it', not 'it does not exist'", tag="empty-result")
            # entered by an edge, must act before speaking -> the pre-generated opening has no tools
            entered_by_edge = any(e.get("target_node_id") == nid for m in nodes.values() for e in _edges(m))
            if (TOOL_FIRST.search(instr) and entered_by_edge and not n.get("is_global")
                    and not n.get("pre_message")):
                add("N10", "warn", nid, "the prompt demands a tool call before speaking, but a node entered "
                    "by an edge opens with a PRE-GENERATED line that cannot call tools. Call the tool in "
                    "the origin node (eval_after_tools), or make this node global", tag="pregen-no-tools")
            if n.get("pre_message") and (n.get("tool_ids") or TOOL_FIRST.search(instr)):
                add("N11", "info", nid, "pre_message hands the turn to the caller: fine for a node that ASKS, "
                    "wrong for one that must ACT first. Check every predecessor makes the line true",
                    tag="pre-message")
            if "get_node_content" in (n.get("tool_ids") or []) and "get_node_content" not in tools:
                add("N12", "warn", nid, "tool_ids lists get_node_content, a built-in enabled by "
                    "faq_prompt_enabled; do not list it (it resolved to nothing on San Roque v12)")
        for tid in n.get("tool_ids") or []:
            if tid not in tools and tid not in BUILTINS:
                add("N13", "warn", f"{nid}.tool_ids", f"{tid!r} is neither in tools[] nor a built-in. Fine "
                    "ONLY if an integration package serves it for this tenant (SINA: login_and_onboard, "
                    "schedule_appointment…); otherwise the worker logs _warn_on_unserved_flow_tools and the "
                    "node never gets it")

    # ---------------------------------------------------------------- tools
    used = {tid for n in nodes.values() for tid in n.get("tool_ids") or []}
    used_names = {tools[t].get("name") for t in used if t in tools}
    after_names = {t for n in nodes.values() for e in n.get("edges") or [] for t in e.get("eval_after_tools") or []}
    for t in doc.get("tools", []) or []:
        name, where = t.get("name") or "?", f"tools[{t.get('name')}]"
        if t.get("type") == "custom" and name not in used_names and name not in after_names:
            add("T01", "warn", where, "orphan: no node lists it, yet it is registered on every node and "
                "pads the model's tool list", tag="orphan-tool")
        if t.get("url") and t.get("type") != "custom":
            add("T02", "error", where, "has a url but type is not 'custom': it is never executed")
        if t.get("type") == "custom" and not TOOL_NAME.fullmatch(name):
            add("T03", "error", where, "name outside the function-calling grammar: a dead tool")
        if t.get("timeout_s") and float(t["timeout_s"]) > 60:
            add("T04", "warn", where, f"timeout_s={t['timeout_s']} is capped at 60 by http_tool.MAX_TIMEOUT_S")
        if (t.get("method") or "GET").upper() != "GET" and not t.get("timeout_s"):
            add("T05", "info", where, "write without timeout_s: default 30 s. Measure the backend's cold write")
        for k, v in (t.get("body_params") or {}).items():
            if not isinstance(v, str):
                continue
            if v.strip().lower() in {"true", "false", "null"}:
                add("T06", "warn", where, f"body_params.{k} = {v!r} is a STRING; a typed backend rejects "
                    "it (San Roque: .NET 400 on every registration). Use a JSON boolean", tag="body-types")
            elif re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
                add("T06", "info", where, f"body_params.{k} = {v!r} is a numeric STRING: check the backend "
                    "contract expects a string, not a number", tag="body-types")
        schema = t.get("args_schema") or {}
        req = set(schema.get("required") or [])
        for prop in (schema.get("properties") or {}):
            if prop not in req:
                add("T07", "warn", where, f"optional argument {prop!r}: the model fills optional arguments "
                    "with \"\", and an argument beats query_params/body_params. If it must not be sent, "
                    "do not offer it — split the tool", tag="optional-arg")
    for where, t in iter_webhooks(doc):
        if where.endswith("prefetch_tools") and (t.get("method") or "GET").upper() != "GET":
            add("T08", "error", where, f"prefetch {t.get('name')} is not a GET: a prefetch may only read")
    hosts = sorted({host(t["url"]) for _, t in iter_webhooks(doc)})
    if len(hosts) > 1:
        add("T09", "warn", "tools", f"webhooks point at {len(hosts)} hosts {hosts}: check none belongs to "
            "another environment", tag="env-mix")

    # ---------------------------------------------------------------- root
    dv = doc.get("default_dynamic_variables") or {}
    blob = json.dumps(doc.get("nodes", []), ensure_ascii=False) + json.dumps(doc.get("tools", []), ensure_ascii=False)
    for var in dv:
        if blob.count(var) == 0:
            add("R01", "info", f"default_dynamic_variables.{var}", "declared and never referenced: noise in "
                "the prompt's writable-variables list")
    if doc.get("global_prompt_enabled") and (doc.get("global_prompt") or "").strip():
        add("R02", "info", "global_prompt", f"reaches ONLY the start node ({start}); call-wide rules (no ids "
            "aloud, one question at a time, no raw errors) must be repeated in every prompt node",
            tag="global-prompt-start-only")
    if doc.get("faq_prompt_enabled") and not (doc.get("faq_prompt") or "").strip():
        add("R03", "warn", "faq_prompt", "FAQ enabled with no answering rules: nothing controls "
            "disambiguation, how many nodes it opens or invention")
    return F


def db_checks(env: str, flow_id: str, doc: dict) -> List[Finding]:
    from flowdb import DB
    db, F = DB(env), []
    if doc.get("faq_prompt_enabled") and not db.collections(flow_id):
        F.append(Finding("D01", "error", "conversation_flow_collections", "FAQ enabled but NO collection "
                         "linked: every factual answer becomes 'no dispongo de esa información' "
                         "(total_docs=0 in the [FAQ] log line)", "clone-kb"))
    ok, detail = db.health(flow_id)
    if not ok:
        F.append(Finding("D02", "error", "conversation_flows", f"unhealthy version stamp: {detail}. "
                         "Written out of band: the worker cache will not see changes", "versioning"))
    users = db.select("api_keys", columns="id", filters=[f"default_conversation_flow_id=eq.{flow_id}"])
    F.append(Finding("D03", "info", "api_keys", f"served as default by {len(users)} api_key(s)"
                     + (" — LIVE customer flow, do not edit in place" if users else " — a clone/sandbox")))
    return F


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--flow", help="flow uuid (with --env)")
    src.add_argument("--file", help="a flow_document JSON file")
    ap.add_argument("--env", choices=["dev", "stg", "prod"], default="stg")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min", choices=SEVERITIES, default="info")
    a = ap.parse_args()
    if a.file:
        doc = json.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))
        doc = doc.get("flow_document", doc)
        findings = lint(doc)
    else:
        from flowdb import DB
        row = DB(a.env).flow(a.flow, columns="name,version,flow_document")
        doc = row["flow_document"]
        findings = lint(doc) + db_checks(a.env, a.flow, doc)
        if not a.json:
            print(f"# {row['name']} v{row['version']} ({a.env})")
    keep = SEVERITIES[: SEVERITIES.index(a.min) + 1]
    findings = sorted((f for f in findings if f.severity in keep),
                      key=lambda f: (SEVERITIES.index(f.severity), f.rule, f.where))
    if a.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=1))
    else:
        for f in findings:
            print(f)
        counts = {s: sum(f.severity == s for f in findings) for s in keep}
        print("\n" + "  ".join(f"{k}={v}" for k, v in counts.items()))
    sys.exit(1 if any(f.severity == "error" for f in findings) else 0)


if __name__ == "__main__":
    main()
