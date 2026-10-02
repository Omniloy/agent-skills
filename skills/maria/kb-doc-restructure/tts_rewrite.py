#!/usr/bin/env python3
"""TTS-friendly rewriting of hard data for the PageIndex `.md` (MAR-1140).

The restructured FAQ `.md` is read aloud by a Spanish TTS engine. Raw tokens
like ``928353535``, ``john.doe@acme.com`` or ``www.acme.com/mi_pagina`` are read
badly (or as one giant number). This module rewrites them into their natural
Spanish *spoken* form, which is then **baked into the `.md`** that goes to
PageIndex. The numeric/textual original is preserved separately via the artifact
storage (MAR-1141), so nothing is lost.

Public helpers (all pure + unit-testable):

    phone_to_words("928353535")     -> "nueve dos ocho treinta y cinco treinta y cinco treinta y cinco"
    phone_to_words("+34928353535")  -> "más treinta y cuatro, nueve dos ocho treinta y cinco
                                        treinta y cinco treinta y cinco"
    email_to_words("john.doe@acme.com")  -> "john punto doe arroba acme punto com"
    url_to_words("www.acme.com/mi_pagina")
        -> "doble uve doble uve doble uve punto acme punto com barra mi barra baja pagina"
    rewrite_text(doc)  -> doc with every email/URL/phone replaced inline

Spanish phone-number reading scheme (documented choice)
-------------------------------------------------------
Spanish speakers group a 9-digit national number as ``NNN NN NN NN`` and read
the first group **digit by digit** and each following pair as a **two-digit
cardinal** (e.g. ``928 35 35 35`` -> "nueve dos ocho · cuarenta · cuarenta ·
cuarenta"). We follow exactly that:

  * If the national number has an ODD count of digits, the first 3 digits form a
    "head" read digit-by-digit; the remaining (even) digits are read in pairs as
    two-digit cardinals. 9 digits -> head 3 + three pairs (the common case).
  * If it has an EVEN count, there is no head; all digits are read in pairs.
  * A pair starting with ``0`` (e.g. ``07``) is read digit-by-digit ("cero
    siete") because "siete" alone would drop the zero.
  * A leading ``+`` becomes "más"; the country code is read as a single cardinal
    ("+34" -> "más treinta y cuatro") and is followed by a comma.
  * An extension ("ext. 23", "extensión 23") is appended as ", extensión <digit
    by digit>". Extensions are read digit-by-digit on purpose (ambiguous → the
    safer, unambiguous choice, per MAR-1140).
"""

from __future__ import annotations

import re

# --- Spanish cardinals (0–999, enough for digits, pairs and country codes) ----

_UNITS = [
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
    "veinte",
    "veintiuno",
    "veintidós",
    "veintitrés",
    "veinticuatro",
    "veinticinco",
    "veintiséis",
    "veintisiete",
    "veintiocho",
    "veintinueve",
]
_TENS = {
    3: "treinta",
    4: "cuarenta",
    5: "cincuenta",
    6: "sesenta",
    7: "setenta",
    8: "ochenta",
    9: "noventa",
}
_HUNDREDS = {
    1: "ciento",
    2: "doscientos",
    3: "trescientos",
    4: "cuatrocientos",
    5: "quinientos",
    6: "seiscientos",
    7: "setecientos",
    8: "ochocientos",
    9: "novecientos",
}

# digit -> spoken word (digit-by-digit reading)
DIGITS = _UNITS[:10]


def number_to_words(n: int) -> str:
    """Spanish cardinal for ``0 <= n <= 999`` (covers two-digit pairs and
    country codes)."""
    if not 0 <= n <= 999:
        raise ValueError(f"number_to_words supports 0..999, got {n}")
    if n < 30:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] if unit == 0 else f"{_TENS[tens]} y {_UNITS[unit]}"
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    head = _HUNDREDS[hundreds]
    return head if rest == 0 else f"{head} {number_to_words(rest)}"


# --- phones -------------------------------------------------------------------

_EXT_RE = re.compile(
    r"^(?P<main>.*?)[\s,;.-]*(?:ext\.?|extensi[oó]n|extension)\s*[:.\-]?\s*"
    r"(?P<ext>\d+)\s*$",
    re.IGNORECASE,
)


def _group_national(digits: str) -> str:
    """Read a bare national number using the documented grouping scheme."""
    tokens: list[str] = []
    n = len(digits)
    if n % 2 == 1:
        head_len = 3 if n >= 3 else n
    else:
        head_len = 0
    for d in digits[:head_len]:
        tokens.append(DIGITS[int(d)])
    rest = digits[head_len:]
    for i in range(0, len(rest), 2):
        pair = rest[i : i + 2]
        if len(pair) == 1:
            tokens.append(DIGITS[int(pair)])
        elif pair[0] == "0":
            tokens.append(DIGITS[0])
            tokens.append(DIGITS[int(pair[1])])
        else:
            tokens.append(number_to_words(int(pair)))
    return " ".join(tokens)


def phone_to_words(raw: str) -> str:
    """Rewrite a phone number into its Spanish spoken form."""
    main, ext = raw, None
    m = _EXT_RE.match(raw.strip())
    if m:
        main, ext = m.group("main"), m.group("ext")

    has_plus = main.strip().startswith("+")
    digits = re.sub(r"\D", "", main)
    if not digits:
        return raw.strip()

    cc = ""
    if has_plus and len(digits) > 9:
        cc, national = digits[:-9], digits[-9:]
    else:
        national = digits

    prefix = ""
    if has_plus:
        prefix = "más"
        if cc:
            prefix = f"más {number_to_words(int(cc))}"
    if cc or (has_plus and not cc):
        # comma after the "más [country code]" lead-in
        national_words = _group_national(national)
        body = f"{prefix}, {national_words}" if prefix else national_words
    else:
        body = _group_national(national)

    if ext:
        ext_words = " ".join(DIGITS[int(d)] for d in ext)
        body = f"{body}, extensión {ext_words}"
    return body.strip().strip(",").strip()


# --- emails -------------------------------------------------------------------

_EMAIL_SYMBOLS = {
    "@": " arroba ",
    ".": " punto ",
    "_": " barra baja ",
    "-": " guion ",
    "+": " más ",
}


def email_to_words(raw: str) -> str:
    """Rewrite an email address into its Spanish spoken form."""
    s = raw.strip().rstrip(".,;:)>")
    out: list[str] = []
    for ch in s:
        if ch in _EMAIL_SYMBOLS:
            out.append(_EMAIL_SYMBOLS[ch])
        elif ch.isdigit():
            out.append(f" {DIGITS[int(ch)]} ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


# --- URLs ---------------------------------------------------------------------

_URL_SYMBOLS = {
    ".": " punto ",
    "/": " barra ",
    "_": " barra baja ",
    "-": " guion ",
    ":": " dos puntos ",
    "?": " interrogación ",
    "=": " igual ",
    "&": " y ",
    "@": " arroba ",
    "#": " almohadilla ",
    "%": " por ciento ",
}


def url_to_words(raw: str) -> str:
    """Rewrite a URL into its Spanish spoken form ('w' -> 'doble uve')."""
    s = re.sub(r"^https?://", "", raw.strip(), flags=re.IGNORECASE)
    s = s.rstrip(".,;:)>")
    out: list[str] = []
    for ch in s:
        if ch.lower() == "w":
            out.append(" doble uve ")
        elif ch in _URL_SYMBOLS:
            out.append(_URL_SYMBOLS[ch])
        elif ch.isdigit():
            out.append(f" {DIGITS[int(ch)]} ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


# --- whole-document baking ----------------------------------------------------
# Order matters: emails and URLs contain '.'/digits that would otherwise be
# misread as phones, so they are rewritten first.

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)>\],]+", re.IGNORECASE)
# +CC optional, 9 national digits possibly grouped by space/.- , optional ext.
_PHONE_RE = re.compile(
    r"(?<![\w])\+?\d{0,3}[\s.\-]?(?:\d[\s.\-]?){8}\d"
    r"(?:\s*(?:ext\.?|extensi[oó]n|extension)\s*[:.\-]?\s*\d+)?"
)


def rewrite_text(text: str) -> str:
    """Bake every email, URL and phone in ``text`` into its spoken form.

    Use this to produce the TTS version of the restructured `.md` before it is
    ingested by PageIndex. Percentages and money amounts are intentionally left
    untouched (the TTS reads them well already and the hard-data audit compares
    them raw)."""
    text = _EMAIL_RE.sub(lambda m: email_to_words(m.group(0)), text)
    text = _URL_RE.sub(lambda m: url_to_words(m.group(0)), text)

    def _phone_sub(m: re.Match) -> str:
        token = m.group(0)
        if len(re.sub(r"\D", "", token)) < 9:
            return token
        leading_ws = token[: len(token) - len(token.lstrip())]
        return leading_ws + phone_to_words(token.strip())

    return _PHONE_RE.sub(_phone_sub, text)


if __name__ == "__main__":  # tiny manual smoke check
    print(phone_to_words("928353535"))
    print(phone_to_words("+34928353535"))
    print(phone_to_words("928 35 35 35 ext. 23"))
    print(email_to_words("john.doe@acme.com"))
    print(url_to_words("www.acme.com/mi_pagina"))
