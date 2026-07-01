"""Resolve a spoken name to an address — but ONLY within the hardcoded allowlist.

Voice transcription is fuzzy ("Nikitha" vs "Nikita"), so matching uses a
similarity ratio plus a substring bonus against each contact's aliases and the
email's local part. A name that doesn't clear the threshold returns 'none'
(the assistant refuses rather than guessing); a near-tie returns 'ambiguous'.
"""

import difflib

from . import config


def _best_score(name, contact):
    name = name.lower().strip()
    candidates = [a.lower() for a in contact["names"]]
    candidates.append(contact["email"].split("@")[0].lower())
    best = 0.0
    for cand in candidates:
        ratio = difflib.SequenceMatcher(None, name, cand).ratio()
        if name and (name in cand or cand in name):
            ratio = max(ratio, 0.9)
        best = max(best, ratio)
    return best


def resolve(name):
    """Return {status, email, candidates}.

    status == 'ok'        -> email is the confident match
    status == 'none'      -> nothing on the allowlist matched; candidates lists all
    status == 'ambiguous' -> two contacts tied; candidates lists the contenders
    """
    name = (name or "").strip()
    all_emails = [c["email"] for c in config.CONTACTS]
    if not name:
        return {"status": "none", "email": None, "candidates": all_emails}

    scored = sorted(
        ((_best_score(name, c), c) for c in config.CONTACTS),
        key=lambda x: x[0],
        reverse=True,
    )
    top_score, top = scored[0]
    if top_score < config.CONTACT_MATCH_THRESHOLD:
        return {"status": "none", "email": None, "candidates": all_emails}

    if len(scored) > 1:
        runner_score, runner = scored[1]
        if runner_score >= config.CONTACT_MATCH_THRESHOLD and (top_score - runner_score) < 0.08:
            return {
                "status": "ambiguous",
                "email": None,
                "candidates": [top["email"], runner["email"]],
            }

    return {"status": "ok", "email": top["email"], "candidates": [top["email"]]}
