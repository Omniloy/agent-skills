#!/usr/bin/env python3
"""Tests that hard_data_audit treats a TTS-rewritten restructured doc as
EQUIVALENT to the raw original (MAR-1140) — no false LOST/INVENTED.

    python3 -m unittest discover -s <skill>/tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hard_data_audit import audit  # noqa: E402
from tts_rewrite import rewrite_text  # noqa: E402


class TestAuditTtsEquivalence(unittest.TestCase):
    def test_baked_phone_not_lost(self):
        original = "Teléfono de citas: 928353535."
        restructured = "# Citas\nTeléfono: nueve dos ocho treinta y cinco treinta y cinco treinta y cinco."
        self.assertEqual(audit(original, restructured), {})

    def test_baked_phone_with_country_code_not_lost(self):
        original = "Llama al +34 928 35 35 35."
        restructured = "# Contacto\nmás treinta y cuatro, nueve dos ocho treinta y cinco treinta y cinco treinta y cinco"
        self.assertEqual(audit(original, restructured), {})

    def test_baked_email_not_lost(self):
        original = "Escribe a john.doe@acme.com"
        restructured = "# Email\njohn punto doe arroba acme punto com"
        self.assertEqual(audit(original, restructured), {})

    def test_baked_url_not_lost(self):
        original = "Más info en https://acme.com/info"
        restructured = "# Web\nacme punto com barra info"
        self.assertEqual(audit(original, restructured), {})

    def test_full_rewrite_text_is_clean(self):
        original = (
            "Citas: 928353535 o +34928353535. "
            "Email john.doe@acme.com. Web https://acme.com/info. "
            "Precio 40 € y descuento 10%."
        )
        restructured = "# FAQ\n" + rewrite_text(original)
        self.assertEqual(audit(original, restructured), {})

    def test_genuinely_lost_phone_still_flagged(self):
        original = "Teléfono: 928353535."
        restructured = "# Citas\nNo hay teléfono publicado."
        report = audit(original, restructured)
        self.assertIn("phone", report)
        self.assertIn("928353535", report["phone"]["lost"])

    def test_wrong_spoken_phone_is_flagged(self):
        # restructured spells a DIFFERENT number -> original one is still lost
        original = "Teléfono: 928353535."
        restructured = "# Citas\nnueve dos ocho treinta y cinco treinta y cinco cincuenta"
        report = audit(original, restructured)
        self.assertIn("phone", report)
        self.assertIn("928353535", report["phone"]["lost"])

    def test_raw_phone_preserved_still_clean(self):
        # backward-compat: no TTS at all
        original = "Teléfono: 928353535."
        restructured = "# Citas\nTeléfono: 928 35 35 35."
        self.assertEqual(audit(original, restructured), {})


if __name__ == "__main__":
    unittest.main()
